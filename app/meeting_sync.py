"""
Teams meeting sync: Microsoft Graph -> MeetingRecord rows.

The Teams pages read meetings and their transcript/recording status from the database.
This module keeps that data fresh:
  * sync_user() re-reads the recent calendar window, removes meetings that disappeared,
    backfills older history a chunk at a time, and checks transcript/recording status for
    meetings that are unchecked or recent enough to change.
  * start_background_sync() runs sync_user() every SYNC_INTERVAL for recently active users.
A row in CalendarSync acts as a per-user lock, so several gunicorn workers (each with its
own background thread) never sync the same user at once.

Everything here uses the app-only Graph token (Calendars.Read, OnlineMeetings.Read.All,
OnlineMeetingTranscript.Read.All, Files.Read.All) so it can run without a user session.
"""
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy import or_, update

from app import db
from app.auth import get_app_token
from app.graph_client import GraphClient
from app.meeting_filter import filter_customer_meetings
from app.models import CalendarSync, MeetingRecord, User
from config import Config

logger = logging.getLogger(__name__)

SYNC_INTERVAL = timedelta(minutes=15)
RECENT_WINDOW_DAYS = 30          # re-read every sync (new / changed / cancelled meetings)
BACKFILL_DAYS = 365              # how far back history is filled in
BACKFILL_CHUNK_DAYS = 60         # history fetched per sync run
RECHECK_RECENT_DAYS = 14         # meetings this recent are re-checked for late transcripts
RECHECK_AFTER = timedelta(minutes=15)
RECHECK_NO_RECORDING_API_AFTER = timedelta(hours=6)  # recordings API refused (permission)
MEDIA_CHECKS_PER_RUN = 60
ACTIVE_USER_DAYS = 30            # background sync only for users who signed in recently
STALE_LOCK_AFTER = timedelta(minutes=10)
MANUAL_SYNC_COOLDOWN = timedelta(seconds=60)
ONEDRIVE_FALLBACK_BUDGET_SECONDS = 6
LOOP_TICK_SECONDS = 60


# ── Lookup helpers (shared with routes) ─────────────────────────────────────

def normalize_subject(s: str) -> str:
    """Normalize a meeting subject for comparison.
    Teams strips special chars like | from recording filenames, so we must
    remove them and collapse whitespace for both sides to match."""
    s = s.lower().strip()
    s = re.sub(r'[|/\\:*?"<>]', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s


def find_recording(meeting: dict, all_recordings: list) -> dict | None:
    """Return the OneDrive file entry matching a calendar event (by date + subject), or None."""
    if not all_recordings:
        return None
    meeting_start = meeting.get("start", {}).get("dateTime", "")
    if not meeting_start:
        return None
    meeting_date = meeting_start[:10].replace("-", "")
    norm_subject = normalize_subject(meeting.get("subject", ""))
    for rec in all_recordings:
        if rec["date"] != meeting_date:
            continue
        norm_rec = normalize_subject(rec["subject"])
        if norm_rec == norm_subject or norm_rec in norm_subject or norm_subject in norm_rec:
            return rec
    return None


def candidate_user_ids(app_client, meeting: dict, signed_in_email: str = "",
                       viewed_user_id: str | None = None) -> list[str]:
    """Ordered Graph user IDs to try for app-only onlineMeeting + transcript lookup."""
    ids: list[str] = []
    seen: set = set()

    def add_uid(uid):
        if uid and uid not in seen:
            seen.add(uid)
            ids.append(uid)

    # The join link names the real organizer — the only option for channel meetings,
    # whose calendar organizer is a group mailbox rather than a user.
    add_uid(GraphClient.organizer_id_from_join_url(
        (meeting.get("onlineMeeting") or {}).get("joinUrl", "")
    ))
    if viewed_user_id:
        add_uid(viewed_user_id)
    organizer_email = (
        meeting.get("organizer", {}).get("emailAddress", {}).get("address", "") or ""
    )
    if organizer_email:
        add_uid(app_client._resolve_user_id_safe(organizer_email))
    if signed_in_email and signed_in_email.lower() != organizer_email.lower():
        add_uid(app_client._resolve_user_id_safe(signed_in_email))
    return ids


def lookup_meeting_media(app_client, meeting: dict, user_ids: list[str]) -> dict:
    """
    Resolve the onlineMeeting via the first user ID that owns it, then report whether it has
    transcripts and return its newest recording (recordings API) as a playback entry.
    """
    result = {
        "has_transcript": False, "recording": None, "user_id": None,
        "online_meeting_id": None, "recording_api_ok": False,
    }
    join_url = (meeting.get("onlineMeeting") or {}).get("joinUrl", "")
    if not join_url:
        return result
    for uid in user_ids:
        try:
            online_meeting = app_client.get_online_meeting_for_user(uid, join_url)
            if not online_meeting:
                continue
            result["user_id"] = uid
            result["online_meeting_id"] = online_meeting["id"]
            result["has_transcript"] = bool(
                app_client.list_transcripts(online_meeting["id"], user_id=uid)
            )
            recs = app_client.list_meeting_recordings(online_meeting["id"], uid)
            result["recording_api_ok"] = recs is not None
            if recs:
                newest = max(recs, key=lambda r: r.get("createdDateTime", ""))
                result["recording"] = {
                    "kind": "api",
                    "user_id": uid,
                    "meeting_id": online_meeting["id"],
                    "recording_id": newest.get("id", ""),
                    "created": newest.get("createdDateTime", ""),
                }
            return result
        except Exception as e:
            logger.debug("Media lookup failed for uid=%s: %s", uid, e)
    return result


# ── Record <-> meeting dict ─────────────────────────────────────────────────

def _parse_graph_dt(value: str) -> datetime | None:
    """'2026-10-19T14:00:00.0000000' (UTC) -> naive UTC datetime."""
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def record_to_meeting(rec: MeetingRecord) -> dict:
    """MeetingRecord -> the Graph-like meeting dict the templates and lookups use."""
    try:
        external = json.loads(rec.external_attendees_json or "[]")
    except ValueError:
        external = []
    return {
        "id": rec.event_id,
        "subject": rec.subject,
        "start": {"dateTime": rec.start_utc.strftime("%Y-%m-%dT%H:%M:%S")},
        "end": {"dateTime": rec.end_utc.strftime("%Y-%m-%dT%H:%M:%S") if rec.end_utc else ""},
        "organizer": {"emailAddress": {"name": rec.organizer_name, "address": rec.organizer_email}},
        "onlineMeeting": {"joinUrl": rec.join_url},
        "external_attendees": external,
        "status_known": rec.has_transcript is not None,
        "has_transcript": bool(rec.has_transcript),
        "has_recording": bool(rec.has_recording),
    }


def record_media(rec: MeetingRecord | None) -> dict | None:
    """Stored lookup result (owner user ID, onlineMeeting ID, recording entry), if any."""
    if not rec or not rec.media_json:
        return None
    try:
        return json.loads(rec.media_json)
    except ValueError:
        return None


def save_media(rec: MeetingRecord, media: dict) -> None:
    rec.has_transcript = bool(media.get("has_transcript"))
    rec.has_recording = bool(media.get("recording"))
    rec.media_json = json.dumps(media)
    rec.status_checked_at = datetime.utcnow()


def get_record(owner_email: str, event_id: str) -> MeetingRecord | None:
    return MeetingRecord.query.filter_by(owner_email=owner_email, event_id=event_id).first()


# ── Calendar upsert ─────────────────────────────────────────────────────────

def _upsert_window(owner_email: str, meetings: list[dict], start: datetime, end: datetime) -> dict:
    """
    Make the DB match Graph for [start, end]: insert new meetings, update changed ones, delete
    ones no longer on the calendar (cancelled, declined, or no longer a customer meeting).
    """
    existing = {
        r.event_id: r for r in MeetingRecord.query.filter(
            MeetingRecord.owner_email == owner_email,
            MeetingRecord.start_utc >= start,
            MeetingRecord.start_utc <= end,
        )
    }
    added = updated = 0
    seen = set()
    now = datetime.utcnow()
    for m in meetings:
        start_utc = _parse_graph_dt(m.get("start", {}).get("dateTime", ""))
        if not start_utc or not m.get("id"):
            continue
        seen.add(m["id"])
        org = m.get("organizer", {}).get("emailAddress", {})
        values = {
            "subject": (m.get("subject") or "")[:500],
            "start_utc": start_utc,
            "end_utc": _parse_graph_dt(m.get("end", {}).get("dateTime", "")),
            "organizer_name": (org.get("name") or "")[:255],
            "organizer_email": (org.get("address") or "")[:255],
            "join_url": (m.get("onlineMeeting") or {}).get("joinUrl", "") or "",
            "external_attendees_json": json.dumps(m.get("external_attendees", [])),
        }
        rec = existing.get(m["id"]) or get_record(owner_email, m["id"])
        if rec is None:
            db.session.add(MeetingRecord(owner_email=owner_email, event_id=m["id"],
                                         first_seen_at=now, updated_at=now, **values))
            added += 1
            continue
        changed = {k: v for k, v in values.items() if getattr(rec, k) != v}
        if changed:
            if "join_url" in changed:  # a different meeting behind the event: re-check status
                rec.has_transcript = rec.has_recording = None
                rec.media_json = rec.status_checked_at = None
            for k, v in changed.items():
                setattr(rec, k, v)
            rec.updated_at = now
            updated += 1

    removed = 0
    for event_id, rec in existing.items():
        if event_id not in seen:
            db.session.delete(rec)
            removed += 1
    db.session.commit()
    return {"added": added, "updated": updated, "removed": removed}


def sync_calendar_window(app_client, owner_email: str, user_id: str,
                         start: datetime, end: datetime) -> dict:
    """Fetch [start, end] of one user's calendar and upsert its customer meetings."""
    events = app_client.list_user_calendar_events(
        user_id, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    )
    meetings = filter_customer_meetings(events, Config.ORG_DOMAIN)
    day_start = datetime(start.year, start.month, start.day)
    day_end = datetime(end.year, end.month, end.day, 23, 59, 59)
    return _upsert_window(owner_email, meetings, day_start, day_end)


# ── Transcript / recording status ───────────────────────────────────────────

def due_for_media_check(owner_email: str, limit: int) -> list[MeetingRecord]:
    """Unchecked meetings, plus recent ones whose transcript/recording may have appeared."""
    now = datetime.utcnow()
    recent = now - timedelta(days=RECHECK_RECENT_DAYS)
    return (
        MeetingRecord.query.filter(
            MeetingRecord.owner_email == owner_email,
            MeetingRecord.start_utc <= now,
            or_(
                MeetingRecord.has_transcript.is_(None),
                db.and_(
                    MeetingRecord.start_utc >= recent,
                    or_(MeetingRecord.has_transcript.is_(False),
                        MeetingRecord.has_recording.is_(False)),
                    MeetingRecord.status_checked_at < now - RECHECK_AFTER,
                ),
                db.and_(
                    MeetingRecord.has_recording.is_(False),
                    MeetingRecord.media_json.like('%"recording_api_ok": false%'),
                    MeetingRecord.status_checked_at < now - RECHECK_NO_RECORDING_API_AFTER,
                ),
            ),
        )
        .order_by(MeetingRecord.start_utc.desc())
        .limit(limit)
        .all()
    )


def check_media(app_client, records: list[MeetingRecord], owner_email: str,
                owner_user_id: str = "") -> None:
    """Look up transcript/recording status for records (Graph calls in parallel) and save."""
    if not records:
        return
    meetings = [record_to_meeting(r) for r in records]

    def media(meeting: dict) -> dict:
        ids = candidate_user_ids(app_client, meeting, signed_in_email=owner_email)
        return lookup_meeting_media(app_client, meeting, ids)

    # Kept modest: Graph throttles (429) on bursts of parallel calls
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(media, meetings))

    # OneDrive file search only where the recordings API couldn't answer (e.g. permission not
    # yet active); when it answered "none", that's authoritative.
    missing = [m for m, r in zip(meetings, results) if not r["recording"] and not r["recording_api_ok"]]
    files = []
    if missing and owner_user_id:
        try:
            files = app_client.build_recording_lookup(
                missing, owner_user_id, Config.ORG_DOMAIN,
                budget_seconds=ONEDRIVE_FALLBACK_BUDGET_SECONDS,
            )
        except Exception:
            logger.exception("OneDrive recording fallback failed")

    for rec, meeting, result in zip(records, meetings, results):
        if not result["recording"]:
            found = find_recording(meeting, files)
            if found and found.get("item_id"):
                result["recording"] = {
                    "kind": "onedrive", "user_id": found["user_id"], "item_id": found["item_id"],
                }
        save_media(rec, result)
    db.session.commit()


# ── Per-user sync ───────────────────────────────────────────────────────────

def _sync_row(owner_email: str) -> CalendarSync:
    row = db.session.get(CalendarSync, owner_email)
    if row is None:
        row = CalendarSync(owner_email=owner_email, last_error="")
        db.session.add(row)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()  # another worker created it first
            row = db.session.get(CalendarSync, owner_email)
    return row


def get_sync_state(owner_email: str) -> CalendarSync | None:
    return db.session.get(CalendarSync, owner_email)


def _claim(owner_email: str, min_age: timedelta) -> bool:
    """Atomically take the per-user sync lock if the last sync is at least min_age old."""
    _sync_row(owner_email)
    now = datetime.utcnow()
    result = db.session.execute(
        update(CalendarSync)
        .where(CalendarSync.owner_email == owner_email)
        .where(or_(CalendarSync.sync_started_at.is_(None),
                   CalendarSync.sync_started_at < now - STALE_LOCK_AFTER))
        .where(or_(CalendarSync.last_synced_at.is_(None),
                   CalendarSync.last_synced_at < now - min_age))
        .values(sync_started_at=now)
    )
    db.session.commit()
    return result.rowcount == 1


def sync_user(owner_email: str, *, min_age: timedelta = SYNC_INTERVAL,
              media_limit: int = MEDIA_CHECKS_PER_RUN, backfill: bool = True,
              recheck_recent: bool = False) -> dict:
    """
    Sync one user's Teams customer meetings. Returns a summary dict; {"skipped": reason}
    when another sync is running or the last one is newer than min_age.
    """
    owner_email = owner_email.lower()
    if not _claim(owner_email, min_age):
        return {"skipped": "recently synced or already running"}

    summary = {"added": 0, "updated": 0, "removed": 0, "checked": 0}
    row = db.session.get(CalendarSync, owner_email)
    try:
        app_token = get_app_token()
        if not app_token:
            raise RuntimeError("app token unavailable")
        app_client = GraphClient(app_token)
        user_id = app_client._resolve_user_id_safe(owner_email)
        if not user_id:
            raise RuntimeError("user not found in the tenant")

        now = datetime.utcnow()
        recent_start = now - timedelta(days=RECENT_WINDOW_DAYS - 1)
        for k, v in sync_calendar_window(app_client, owner_email, user_id, recent_start, now).items():
            summary[k] += v

        # History, one chunk per run so each sync stays short
        if row.synced_from is None:
            row.synced_from = datetime(recent_start.year, recent_start.month, recent_start.day)
        oldest = now - timedelta(days=BACKFILL_DAYS)
        if backfill and row.synced_from > oldest:
            chunk_end = row.synced_from - timedelta(days=1)
            chunk_start = max(chunk_end - timedelta(days=BACKFILL_CHUNK_DAYS - 1), oldest)
            for k, v in sync_calendar_window(app_client, owner_email, user_id, chunk_start, chunk_end).items():
                summary[k] += v
            row.synced_from = datetime(chunk_start.year, chunk_start.month, chunk_start.day)

        if recheck_recent:
            # Manual sync: re-check the last two days right away (transcripts arrive late)
            MeetingRecord.query.filter(
                MeetingRecord.owner_email == owner_email,
                MeetingRecord.start_utc >= now - timedelta(days=2),
                or_(MeetingRecord.has_transcript.is_(False), MeetingRecord.has_recording.is_(False)),
            ).update({"has_transcript": None}, synchronize_session=False)

        if media_limit:
            records = due_for_media_check(owner_email, media_limit)
            check_media(app_client, records, owner_email, user_id)
            summary["checked"] = len(records)

        row.last_synced_at = datetime.utcnow()
        row.last_error = ""
        logger.info("Synced %s: %s", owner_email, summary)
    except Exception as e:
        db.session.rollback()
        row = db.session.get(CalendarSync, owner_email)
        row.last_error = f"{type(e).__name__}: {e}"[:500]
        logger.exception("Sync failed for %s", owner_email)
        summary["error"] = "Sync failed"
    finally:
        row.sync_started_at = None
        db.session.commit()
    return summary


def ensure_backfill(owner_email: str, until: datetime) -> bool:
    """
    Synchronously fill history back to `until` (used when the user scrolls or filters past
    what the background backfill has reached). Returns True if anything was fetched.
    """
    owner_email = owner_email.lower()
    row = _sync_row(owner_email)
    if row.synced_from is not None and row.synced_from <= until:
        return False
    app_token = get_app_token()
    if not app_token:
        return False
    app_client = GraphClient(app_token)
    user_id = app_client._resolve_user_id_safe(owner_email)
    if not user_id:
        return False
    end = (row.synced_from - timedelta(days=1)) if row.synced_from else datetime.utcnow()
    sync_calendar_window(app_client, owner_email, user_id, until, end)
    row = db.session.get(CalendarSync, owner_email)
    row.synced_from = datetime(until.year, until.month, until.day)
    db.session.commit()
    return True


# ── Background loop ─────────────────────────────────────────────────────────

def _active_users() -> list[str]:
    """Org users who signed in recently (others, e.g. sample data, are never synced)."""
    since = datetime.utcnow() - timedelta(days=ACTIVE_USER_DAYS)
    suffix = f"@{Config.ORG_DOMAIN}".lower()
    return [
        u.email.lower() for u in User.query.filter(User.last_login >= since).all()
        if u.email.lower().endswith(suffix)
    ]


def _loop(app) -> None:
    time.sleep(10)  # let the app finish starting
    while True:
        try:
            with app.app_context():
                for email in _active_users():
                    sync_user(email)
                db.session.remove()
        except Exception:
            logger.exception("Background sync tick failed")
        time.sleep(LOOP_TICK_SECONDS)


_started = False


_start_lock = threading.Lock()


def start_background_sync(app) -> None:
    """
    Start the 15-minute sync thread once per serving process. Called from the first request
    (not create_app) so the debug reloader's watcher process never starts one.
    Disable with MEETING_SYNC_ENABLED=false.
    """
    global _started
    if _started or app.config.get("TESTING") or os.getenv("MEETING_SYNC_ENABLED", "true").lower() != "true":
        return
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, args=(app,), name="meeting-sync", daemon=True).start()
    logger.info("Background meeting sync started (every %s)", SYNC_INTERVAL)
