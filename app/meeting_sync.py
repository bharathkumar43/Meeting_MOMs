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
from app.graph_client import GraphClient, clear_recording_search_cache
from app.ttl_cache import TTLCache
from app.meeting_filter import filter_customer_meetings
from app.models import CalendarSync, MeetingAttendance, MeetingRecord, MeetingUpload, User
from config import Config

logger = logging.getLogger(__name__)

SYNC_INTERVAL = timedelta(minutes=15)
# Re-read every sync (new / changed / cancelled meetings); also the first-sync window.
RECENT_WINDOW_DAYS = Config.SYNC_INITIAL_DAYS
# History older than the first window is only fetched when backfill is enabled.
BACKFILL_ENABLED = Config.SYNC_BACKFILL_ENABLED
BACKFILL_DAYS = Config.SYNC_BACKFILL_DAYS
BACKFILL_CHUNK_DAYS = 60         # history fetched per sync run
RECHECK_RECENT_DAYS = 14         # meetings this recent are re-checked for late transcripts
RECHECK_AFTER = timedelta(minutes=15)
RECHECK_NO_RECORDING_API_AFTER = timedelta(hours=6)  # recordings API refused (permission)
MEDIA_CHECKS_PER_RUN = Config.SYNC_MEDIA_CHECKS_PER_RUN
ACTIVE_USER_DAYS = Config.SYNC_ACTIVE_USER_DAYS  # 0 = every org user who ever signed in
STALE_LOCK_AFTER = timedelta(minutes=10)
MANUAL_SYNC_COOLDOWN = timedelta(seconds=60)
ONEDRIVE_FALLBACK_BUDGET_SECONDS = 6
LOOP_TICK_SECONDS = 60


# ── Meeting source (Teams / Zoom) ───────────────────────────────────────────

SOURCES = {"teams": "Teams", "zoom": "Zoom"}
_TEAMS_HOSTS = ("teams.microsoft.com", "teams.live.com")
_LINK_PATTERNS = {
    "zoom": re.compile(r"https?://[\w.-]*zoom\.us/(?:j|my|w)/[\w./?=&%-]+", re.I),
}
# SQL LIKE patterns used to filter stored rows by source (via their join link)
SOURCE_LIKE = {
    "teams": ["%teams.microsoft.com%", "%teams.live.com%"],
    "zoom": ["%zoom.us%"],
}


def classify_event(event: dict) -> tuple[str, str] | None:
    """(source, join link) for a calendar event, or None if it isn't an online meeting."""
    join = (event.get("onlineMeeting") or {}).get("joinUrl") or ""
    if event.get("isOnlineMeeting") and any(h in join for h in _TEAMS_HOSTS):
        return "teams", join
    text = " ".join([
        (event.get("location") or {}).get("displayName") or "",
        event.get("onlineMeetingUrl") or "",
        event.get("bodyPreview") or "",
    ])
    for source, pattern in _LINK_PATTERNS.items():
        match = pattern.search(text)
        if match:
            return source, match.group(0)
    if event.get("isOnlineMeeting") and join:
        return "teams", join
    return None


def source_of_join_url(join_url: str) -> str:
    url = (join_url or "").lower()
    if "zoom.us" in url:
        return "zoom"
    return "teams"


def source_filter(query, source: str | None):
    """Restrict a MeetingRecord query to one source (None/'' = all sources)."""
    patterns = SOURCE_LIKE.get(source or "")
    if not patterns:
        return query
    return query.filter(or_(*[MeetingRecord.join_url.like(p) for p in patterns]))


# ── Lookup helpers (shared with routes) ─────────────────────────────────────

def normalize_subject(s: str) -> str:
    """Normalize a meeting subject for comparison.
    Teams strips special chars like | from recording filenames, so we must
    remove them and collapse whitespace for both sides to match."""
    s = s.lower().strip()
    s = re.sub(r'[|/\\:*?"<>]', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s


MIN_CONTAINED_SUBJECT = 8  # shorter subjects ("Sync") would be "contained" in most file names


def find_recording(meeting: dict, all_recordings: list, owner_user_id: str = "",
                   organizer_user_id: str = "") -> dict | None:
    """
    Return the OneDrive file entry matching a calendar event (by date + subject), or None.
    Only files in the owner's OneDrive, or the organizer's when the caller verified that the
    event's organizer and the join link's Oid are the same person, qualify: the shared
    search pool spans several people.
    """
    if not all_recordings:
        return None
    meeting_start = meeting.get("start", {}).get("dateTime", "")
    if not meeting_start:
        return None
    link_oid = GraphClient.organizer_id_from_join_url(
        (meeting.get("onlineMeeting") or {}).get("joinUrl", "") or "")
    verified_organizer = organizer_user_id if (
        organizer_user_id and link_oid and organizer_user_id.lower() == link_oid.lower()) else ""
    allowed = {u.lower() for u in (owner_user_id, verified_organizer) if u}
    meeting_date = meeting_start[:10].replace("-", "")
    norm_subject = normalize_subject(meeting.get("subject", ""))
    if not norm_subject:
        return None
    for rec in all_recordings:
        if rec["date"] != meeting_date or (rec.get("user_id") or "").lower() not in allowed:
            continue
        norm_rec = normalize_subject(rec["subject"])
        if norm_rec == norm_subject or (
            min(len(norm_rec), len(norm_subject)) >= MIN_CONTAINED_SUBJECT
            and (norm_rec in norm_subject or norm_subject in norm_rec)
        ):
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


OCCURRENCE_BEFORE = timedelta(hours=1)
OCCURRENCE_AFTER = timedelta(hours=4)


def for_occurrence(items: list[dict] | None, meeting: dict) -> list[dict]:
    """
    Transcripts/recordings (Graph items with createdDateTime) that belong to THIS calendar
    occurrence, newest first. A recurring Teams meeting shares one onlineMeeting across the
    whole series, so its transcript and recording lists contain every week's items — taking
    the first one would attach another week's transcript to this meeting.
    """
    if not items:
        return []
    start = _parse_graph_dt((meeting.get("start") or {}).get("dateTime", ""))
    if not start:
        return list(items)
    end = _parse_graph_dt((meeting.get("end") or {}).get("dateTime", "")) or start + timedelta(hours=1)
    low, high = start - OCCURRENCE_BEFORE, end + OCCURRENCE_AFTER
    matched = [
        item for item in items
        if (created := _parse_graph_dt(item.get("createdDateTime", ""))) and low <= created <= high
    ]
    return sorted(matched, key=lambda i: i.get("createdDateTime", ""), reverse=True)


# ── Zoom (cloud recordings in the CloudFuze Zoom account) ──────────────────

_ZOOM_ID_RE = re.compile(r"zoom\.us/(?:j|w|s|my)/(\d{9,12})", re.I)
_ZOOM_INDEX = TTLCache(ttl_seconds=600)  # account recordings per month window
ZOOM_INDEX_MONTH_DAYS = 30


def zoom_meeting_id(join_url: str) -> str | None:
    match = _ZOOM_ID_RE.search(join_url or "")
    return match.group(1) if match else None


def _zoom_recordings_between(zoom, start: datetime, end: datetime) -> list[dict] | None:
    """Account cloud recordings whose start falls in [start, end] (month chunks, cached)."""
    found: list[dict] = []
    cursor = datetime(start.year, start.month, start.day)
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=ZOOM_INDEX_MONTH_DAYS - 1), end)
        key = (cursor.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d"))
        rows = _ZOOM_INDEX.get(key)
        if rows is None:
            rows = zoom.list_account_recordings(*key)
            if rows is None:
                return None
            _ZOOM_INDEX.set(key, rows)
        found.extend(rows)
        cursor = chunk_end + timedelta(days=1)
    return found


_ZOOM_PARTICIPANTS = TTLCache(ttl_seconds=600)  # recording uuid -> participants (or _UNKNOWN)
_UNKNOWN = "unknown"


def _zoom_recording_belongs_to(zoom, rec: dict, owner_email: str) -> bool | None:
    """True if the owner hosted or attended this recording; None if Zoom couldn't say."""
    if (rec.get("host_email") or "").strip().lower() == owner_email:
        return True
    uuid = rec.get("uuid") or ""
    people = _ZOOM_PARTICIPANTS.get(uuid)
    if people is None:  # cached for 10 minutes either way, so page views don't hammer Zoom
        people = zoom.get_past_meeting_participants(uuid)
        _ZOOM_PARTICIPANTS.set(uuid, _UNKNOWN if people is None else people)
    if people is None or people == _UNKNOWN:
        return None
    return any((p.get("user_email") or "").strip().lower() == owner_email for p in people)


def lookup_zoom_media(zoom, meeting: dict, owner_email: str) -> dict:
    """
    Video + transcript for a Zoom invite: the account recording with the invite's meeting ID
    that started during this calendar slot. Personal meeting rooms reuse one ID for many
    meetings, so the time match (not the ID alone) decides. The join link comes from calendar
    text anyone can write, so a recording is only attached if the owner hosted or attended it.
    """
    owner_email = (owner_email or "").strip().lower()
    result = {
        "source": "zoom", "has_transcript": False, "recording": None,
        "transcript_url": None, "recording_api_ok": False, "bound_to": owner_email,
    }
    mid = zoom_meeting_id((meeting.get("onlineMeeting") or {}).get("joinUrl", ""))
    start = _parse_graph_dt((meeting.get("start") or {}).get("dateTime", ""))
    if not mid or not start or zoom is None:
        return result
    end = _parse_graph_dt((meeting.get("end") or {}).get("dateTime", "")) or start + timedelta(hours=1)
    recordings = _zoom_recordings_between(zoom, start - timedelta(days=1), end + timedelta(days=1))
    if recordings is None:
        return result
    result["recording_api_ok"] = True
    low, high = start - OCCURRENCE_BEFORE, end + OCCURRENCE_AFTER
    candidates = []
    for rec in recordings:
        if str(rec.get("id")) != mid:
            continue
        rec_start = _parse_graph_dt((rec.get("start_time") or "").replace("Z", ""))
        if rec_start and low <= rec_start <= high:
            candidates.append((abs((rec_start - start).total_seconds()), rec))
    if not candidates:
        return result
    # Several recordings in one slot (stopped/restarted): prefer one with a transcript, then closest
    candidates.sort(key=lambda c: (
        not any(f.get("file_type") == "TRANSCRIPT" for f in c[1].get("recording_files", [])), c[0]
    ))
    undecided = False
    for _, rec in candidates:
        belongs = _zoom_recording_belongs_to(zoom, rec, owner_email)
        if belongs:
            return {**result, **_zoom_media_from_recording(rec)}
        undecided = undecided or belongs is None
    if undecided:
        result["recording_api_ok"] = False  # participants unknown: look again later
    return result


def _zoom_media_from_recording(rec: dict) -> dict:
    """Media entry (video + transcript download URLs) from one Zoom cloud recording."""
    files = [f for f in rec.get("recording_files", []) if (f.get("status") or "").lower() == "completed"]
    video = next((f for f in files if f.get("file_type") == "MP4"), None)
    transcript = next((f for f in files if f.get("file_type") == "TRANSCRIPT"), None)
    media = {
        "source": "zoom", "recording_api_ok": True, "zoom_uuid": rec.get("uuid"),
        "host_email": rec.get("host_email"), "has_transcript": bool(transcript),
        "transcript_url": transcript.get("download_url") if transcript else None,
        "recording": None,
    }
    if video:
        media["recording"] = {"kind": "zoom", "download_url": video.get("download_url"),
                              "created": rec.get("start_time", "")}
    return media


# Zoom meetings an employee hosted and recorded with no Outlook invite behind them
# (scheduled in the Zoom app, personal room, ad hoc) are stored under this event-ID prefix.
ZOOM_HOSTED_PREFIX = "zoom:"
_ZOOM_USER_CACHE = TTLCache(ttl_seconds=3600)      # email -> is a CloudFuze Zoom user
_ZOOM_NOT_CUSTOMER = TTLCache(ttl_seconds=86400)   # recording uuid -> no external participants


def _external_participants(zoom, uuid: str) -> list[dict] | None:
    """
    Participants from outside the org, or None if Zoom couldn't say. Guests often join
    without an email, so those count as external when Zoom marks them non-internal.
    """
    people = zoom.get_past_meeting_participants(uuid)
    if people is None:
        return None
    domain = f"@{Config.ORG_DOMAIN.lower()}"
    external, seen = [], set()
    for p in people:
        email = (p.get("user_email") or "").strip().lower()
        name = (p.get("name") or "").strip()
        if email:
            is_external = not email.endswith(domain)
        else:
            is_external = p.get("internal_user") is False and bool(name)
        key = email or name.lower()
        if is_external and key not in seen:
            seen.add(key)
            external.append({"name": name or email, "email": email})
    return external


def sync_zoom_hosted(owner_email: str, start: datetime, end: datetime) -> dict:
    """
    Store customer Zoom meetings the employee hosted and cloud-recorded in [start, end] that
    the calendar sync didn't already bring in (no Outlook invite). Same customer rule as
    Teams: at least one participant from outside the org.
    """
    summary = {"added": 0}
    zoom = zoom_client_or_none()
    if zoom is None:
        return summary
    is_user = _ZOOM_USER_CACHE.get(owner_email)
    if is_user is None:
        is_user = zoom.user_exists(owner_email)
        _ZOOM_USER_CACHE.set(owner_email, is_user)
    if not is_user:
        return summary

    recordings: list[dict] = []
    cursor = start
    while cursor <= end:  # Zoom allows at most one month per call
        chunk_end = min(cursor + timedelta(days=ZOOM_INDEX_MONTH_DAYS - 1), end)
        recordings.extend(zoom.list_user_recordings(
            owner_email, cursor.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        cursor = chunk_end + timedelta(days=1)

    now = datetime.utcnow()
    for rec in recordings:
        uuid, mid = rec.get("uuid"), str(rec.get("id") or "")
        rec_start = _parse_graph_dt((rec.get("start_time") or "").replace("Z", ""))
        if not uuid or not mid or not rec_start or uuid in _ZOOM_NOT_CUSTOMER:
            continue
        event_id = f"{ZOOM_HOSTED_PREFIX}{uuid}"
        if get_record(owner_email, event_id):
            continue
        # Already here through the calendar invite for the same Zoom meeting and slot
        if MeetingRecord.query.filter(
            MeetingRecord.owner_email == owner_email,
            MeetingRecord.join_url.like(f"%{mid}%"),
            MeetingRecord.start_utc >= rec_start - OCCURRENCE_AFTER - timedelta(hours=1),
            MeetingRecord.start_utc <= rec_start + OCCURRENCE_BEFORE,
        ).first():
            continue
        external = _external_participants(zoom, uuid)
        if external is None:
            continue  # try again next run
        if not external:
            _ZOOM_NOT_CUSTOMER.set(uuid, True)
            continue
        media = {**_zoom_media_from_recording(rec), "bound_to": owner_email.lower()}  # their own recording
        db.session.add(MeetingRecord(
            owner_email=owner_email, event_id=event_id,
            subject=(rec.get("topic") or "Zoom meeting")[:500],
            start_utc=rec_start,
            end_utc=rec_start + timedelta(minutes=int(rec.get("duration") or 0)),
            organizer_name=(rec.get("host_email") or owner_email)[:255],
            organizer_email=(rec.get("host_email") or owner_email)[:255],
            join_url=f"https://zoom.us/j/{mid}",
            external_attendees_json=json.dumps(external),
            has_transcript=media["has_transcript"], has_recording=bool(media["recording"]),
            media_json=json.dumps(media), status_checked_at=now,
            first_seen_at=now, updated_at=now,
        ))
        summary["added"] += 1
    db.session.commit()
    return summary


def zoom_client_or_none():
    from app.zoom_auth import get_zoom_access_token
    from app.zoom_client import ZoomClient
    token = get_zoom_access_token()
    return ZoomClient(token) if token else None


_RECORDING_NAME_TIME_RE = re.compile(r"-(\d{8}_\d{6})UTC-")


def find_channel_recording(app_client, meeting: dict) -> dict | None:
    """
    Recording of a Teams channel meeting. These are saved to the team's SharePoint site and
    the meeting recordings API doesn't list them, so search the site for the subject and
    pick the file whose timestamp ('...-20261021_135833UTC-Meeting Recording.mp4') falls in
    this occurrence's slot. Only used when the organizer is a team, not a person.
    """
    organizer = (meeting.get("organizer") or {}).get("emailAddress", {}).get("address", "")
    if not organizer.lower().endswith(f"@{Config.ORG_DOMAIN.lower()}"):
        return None
    if app_client._resolve_user_id_safe(organizer):
        return None  # a person organized it: their recordings come from the API / OneDrive
    drive_id = app_client.channel_site_drive_id(organizer)
    start = _parse_graph_dt((meeting.get("start") or {}).get("dateTime", ""))
    if not drive_id or not start:
        return None
    end = _parse_graph_dt((meeting.get("end") or {}).get("dateTime", "")) or start + timedelta(hours=1)
    low, high = start - OCCURRENCE_BEFORE, end + OCCURRENCE_AFTER
    best = None
    for f in app_client.search_drive_recordings(drive_id, meeting.get("subject", "")):
        match = _RECORDING_NAME_TIME_RE.search(f.get("name", ""))
        recorded = (datetime.strptime(match.group(1), "%Y%m%d_%H%M%S") if match
                    else _parse_graph_dt((f.get("createdDateTime") or "").replace("Z", "")))
        if recorded and low <= recorded <= high:
            gap = abs((recorded - start).total_seconds())
            if best is None or gap < best[0]:
                best = (gap, f, recorded)
    if best is None:
        return None
    return {"kind": "sharepoint", "drive_id": drive_id, "item_id": best[1]["id"],
            "created": best[2].strftime("%Y-%m-%dT%H:%M:%SZ")}


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
            transcripts = for_occurrence(
                app_client.list_transcripts(online_meeting["id"], user_id=uid), meeting
            )
            result["has_transcript"] = bool(transcripts)
            result["transcript_id"] = transcripts[0]["id"] if transcripts else None
            raw_recs = app_client.list_meeting_recordings(online_meeting["id"], uid)
            result["recording_api_ok"] = raw_recs is not None
            recs = for_occurrence(raw_recs, meeting)
            if recs:
                newest = recs[0]
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
        "source": source_of_join_url(rec.join_url),
        "status_known": rec.has_transcript is not None,
        "has_transcript": bool(rec.has_transcript),
        "has_recording": bool(rec.has_recording),
        # True / False = joined or not; None = unknown or not checked yet
        "attended": rec.attendance.attended if rec.attendance else None,
    }


def record_media(rec: MeetingRecord | None) -> dict | None:
    """Stored lookup result (owner user ID, onlineMeeting ID, recording entry), if any."""
    if not rec or not rec.media_json:
        return None
    try:
        return json.loads(rec.media_json)
    except ValueError:
        return None


def zoom_media_unchecked(rec: MeetingRecord, media: dict | None) -> bool:
    """Stored Zoom invite media saved before the host/participant check (never serve it)."""
    return (media is not None and source_of_join_url(rec.join_url) == "zoom"
            and not rec.event_id.startswith(ZOOM_HOSTED_PREFIX)
            and media.get("bound_to") != (rec.owner_email or "").lower())


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
            # Zoom-hosted meetings have no calendar event; never prune them here
            ~MeetingRecord.event_id.like(f"{ZOOM_HOSTED_PREFIX}%"),
        )
    }
    added = updated = 0
    seen = set()
    now = datetime.utcnow()
    # Meetings stored outside this window (start moved): one query instead of one per meeting
    missing = {m["id"] for m in meetings if m.get("id") and m["id"] not in existing}
    elsewhere = {
        r.event_id: r for r in MeetingRecord.query.filter(
            MeetingRecord.owner_email == owner_email, MeetingRecord.event_id.in_(missing)
        )
    } if missing else {}
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

        rec = existing.get(m["id"]) or elsewhere.get(m["id"])
        if rec is None:
            rec = MeetingRecord(owner_email=owner_email, event_id=m["id"],
                                first_seen_at=now, updated_at=now, **values)
            db.session.add(rec)
            elsewhere[m["id"]] = rec  # a repeated event ID updates this row, as before
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
            # Keep meetings someone uploaded a video/transcript for, even if they left the calendar
            if MeetingUpload.query.filter_by(meeting_record_id=rec.id).first() is None:
                db.session.delete(rec)
                removed += 1
    db.session.commit()
    return {"added": added, "updated": updated, "removed": removed}


def sync_calendar_window(app_client, owner_email: str, user_id: str,
                         start: datetime, end: datetime) -> dict:
    """Fetch [start, end] of one user's calendar and upsert its customer meetings
    (Teams online meetings plus Zoom invites found by their link)."""
    events = app_client.list_user_calendar_events(
        user_id, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), online_only=False
    )
    online = []
    for event in events:
        if event.get("isCancelled") or (event.get("subject") or "").startswith("Canceled:"):
            continue  # cancelled meetings are not listed (and later pruned)
        classified = classify_event(event)
        if classified:
            event["onlineMeeting"] = {"joinUrl": classified[1]}
            online.append(event)
    meetings = filter_customer_meetings(online, Config.ORG_DOMAIN)
    day_start = datetime(start.year, start.month, start.day)
    day_end = datetime(end.year, end.month, end.day, 23, 59, 59)
    return _upsert_window(owner_email, meetings, day_start, day_end)


# ── Transcript / recording status ───────────────────────────────────────────

def due_for_media_check(owner_email: str, limit: int) -> list[MeetingRecord]:
    """Unchecked meetings, plus recent ones whose transcript/recording may have appeared."""
    now = datetime.utcnow()
    recent = now - timedelta(days=RECHECK_RECENT_DAYS)
    teams_only = MeetingRecord.query.filter(or_(
        *[MeetingRecord.join_url.like(p) for p in SOURCE_LIKE["teams"] + SOURCE_LIKE["zoom"]]
    ))
    return (
        teams_only.filter(
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
    """Look up transcript/recording status for records (Graph calls in parallel) and save.
    Zoom invites are matched against the CloudFuze Zoom account's cloud recordings."""
    if not records:
        return
    zoom_records = [r for r in records if source_of_join_url(r.join_url) == "zoom"]
    if zoom_records:
        zoom = zoom_client_or_none()
        for rec in zoom_records:
            save_media(rec, lookup_zoom_media(zoom, record_to_meeting(rec), rec.owner_email))
        db.session.commit()
        records = [r for r in records if source_of_join_url(r.join_url) != "zoom"]
        if not records:
            return
    meetings = [record_to_meeting(r) for r in records]

    def media(meeting: dict) -> dict:
        ids = candidate_user_ids(app_client, meeting, signed_in_email=owner_email)
        result = lookup_meeting_media(app_client, meeting, ids)
        if not result["recording"]:
            try:
                result["recording"] = find_channel_recording(app_client, meeting)
            except Exception:
                logger.exception("Channel recording lookup failed")
        return result

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
            organizer = ((meeting.get("organizer") or {}).get("emailAddress") or {}).get("address", "")
            organizer_uid = app_client._resolve_user_id_safe(organizer) if (
                organizer and organizer.lower().endswith(f"@{Config.ORG_DOMAIN.lower()}")) else None
            found = find_recording(meeting, files, owner_user_id, organizer_uid or "")
            if found and found.get("item_id"):
                result["recording"] = {
                    "kind": "onedrive", "user_id": found["user_id"], "item_id": found["item_id"],
                }
        save_media(rec, result)
    db.session.commit()


# ── Attendance (did the owner actually join?) ───────────────────────────────

ATTENDANCE_CHECKS_PER_RUN = 40
ATTENDANCE_GRACE = timedelta(minutes=10)      # reports appear a few minutes after the end
ATTENDANCE_GIVE_UP_AFTER = timedelta(days=1)  # still no report a day later: unknown, stop


def _in_slot(rec: MeetingRecord, when: datetime | None) -> bool:
    end = rec.end_utc or rec.start_utc + timedelta(hours=1)
    return bool(when) and rec.start_utc - OCCURRENCE_BEFORE <= when <= end + OCCURRENCE_AFTER


def _teams_attended(app_client, rec: MeetingRecord, owner_uid: str | None) -> bool | None:
    """From the Teams attendance report for this occurrence; None if there is no report."""
    media = record_media(rec) or {}
    uid, mid = media.get("user_id"), media.get("online_meeting_id")
    if not uid or not mid:
        return None
    reports = app_client.list_attendance(uid, mid)
    if not reports:
        return None
    in_slot = [r for r in reports
               if _in_slot(rec, _parse_graph_dt(r.get("meetingStartDateTime") or ""))]
    if not in_slot or any(r.get("records") is None for r in in_slot):
        return None
    owner = rec.owner_email.lower()
    for r in in_slot:
        for person in r["records"]:
            if (person.get("emailAddress") or "").lower() == owner:
                return True
            if owner_uid and (person.get("identity") or {}).get("id") == owner_uid:
                return True
    return False


def _zoom_attended(zoom, rec: MeetingRecord, owner_name: str) -> bool | None:
    """From Zoom's participant list for this occurrence; None if Zoom has none for us."""
    if zoom is None:
        return None
    uuid = (record_media(rec) or {}).get("zoom_uuid")
    if not uuid:  # not recorded: find this occurrence among the meeting ID's past instances
        mid = zoom_meeting_id(rec.join_url)
        instances = zoom.list_past_instances(mid) if mid else None
        for inst in instances or []:
            if _in_slot(rec, _parse_graph_dt((inst.get("start_time") or "").replace("Z", ""))):
                uuid = inst.get("uuid")
                break
    if not uuid:
        return None
    people = zoom.get_past_meeting_participants(uuid)
    if not people:
        return None
    owner, name = rec.owner_email.lower(), (owner_name or "").strip().lower()
    for p in people:
        email = (p.get("user_email") or "").lower()
        if email == owner or (not email and name and (p.get("name") or "").strip().lower() == name):
            return True
    return False


def check_attendance(app_client, owner_email: str, owner_uid: str | None, limit: int) -> int:
    """Fill in MeetingAttendance for ended meetings not yet settled. Returns how many checked."""
    now = datetime.utcnow()
    candidates = (
        MeetingRecord.query.outerjoin(MeetingAttendance)
        .filter(
            MeetingRecord.owner_email == owner_email,
            MeetingRecord.start_utc <= now - ATTENDANCE_GRACE,
            or_(MeetingAttendance.meeting_record_id.is_(None),
                db.and_(MeetingAttendance.final.is_(False),
                        MeetingAttendance.checked_at < now - RECHECK_AFTER)),
        )
        .order_by(MeetingRecord.start_utc.desc())
        .limit(limit * 2)
        .all()
    )
    user = User.query.filter_by(email=owner_email).first()
    owner_name = user.name if user else ""
    zoom = None
    checked = 0
    for rec in candidates:
        ended = rec.end_utc or rec.start_utc + timedelta(hours=1)
        if ended > now - ATTENDANCE_GRACE:
            continue
        is_zoom = source_of_join_url(rec.join_url) == "zoom"
        if not is_zoom and rec.has_transcript is None:
            continue  # Teams: wait for the media check to find the onlineMeeting first
        if is_zoom and zoom is None:
            zoom = zoom_client_or_none()
        attended = (_zoom_attended(zoom, rec, owner_name) if is_zoom
                    else _teams_attended(app_client, rec, owner_uid))
        media = record_media(rec) or {}
        no_report_possible = not is_zoom and not media.get("online_meeting_id")  # client-organized
        final = attended is not None or no_report_possible or now - ended > ATTENDANCE_GIVE_UP_AFTER
        row = rec.attendance or MeetingAttendance(meeting_record_id=rec.id)
        row.attended, row.final, row.checked_at = attended, final, now
        db.session.add(row)
        checked += 1
        if checked >= limit:
            break
    db.session.commit()
    return checked


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
        if row.last_synced_at and row.last_synced_at - timedelta(days=1) < recent_start:
            recent_start = row.last_synced_at - timedelta(days=1)  # catch up after downtime
        if not BACKFILL_ENABLED and row.synced_from and recent_start < row.synced_from:
            recent_start = row.synced_from  # never before the first sync window
        for k, v in sync_calendar_window(app_client, owner_email, user_id, recent_start, now).items():
            summary[k] += v
        try:
            summary["added"] += sync_zoom_hosted(owner_email, recent_start, now)["added"]
        except Exception:
            db.session.rollback()
            logger.exception("Zoom hosted-recording sync failed for %s", owner_email)

        # History, one chunk per run so each sync stays short
        if row.synced_from is None:
            row.synced_from = datetime(recent_start.year, recent_start.month, recent_start.day)
        oldest = now - timedelta(days=BACKFILL_DAYS)
        if BACKFILL_ENABLED and backfill and row.synced_from > oldest:
            chunk_end = row.synced_from - timedelta(days=1)
            chunk_start = max(chunk_end - timedelta(days=BACKFILL_CHUNK_DAYS - 1), oldest)
            for k, v in sync_calendar_window(app_client, owner_email, user_id, chunk_start, chunk_end).items():
                summary[k] += v
            row.synced_from = datetime(chunk_start.year, chunk_start.month, chunk_start.day)

        if recheck_recent:
            # Manual sync: re-check the last two days right away (transcripts and recordings
            # arrive late). Drop cached OneDrive search results so a just-finished recording
            # is seen now rather than after the cache expires.
            clear_recording_search_cache()
            _ZOOM_INDEX.clear()  # a just-finished Zoom cloud recording shows up now
            MeetingRecord.query.filter(
                or_(*[MeetingRecord.join_url.like(p)
                      for p in SOURCE_LIKE["teams"] + SOURCE_LIKE["zoom"]]),
                MeetingRecord.owner_email == owner_email,
                MeetingRecord.start_utc >= now - timedelta(days=2),
                or_(MeetingRecord.has_transcript.is_(False), MeetingRecord.has_recording.is_(False)),
            ).update({"has_transcript": None}, synchronize_session=False)

        if media_limit:
            records = due_for_media_check(owner_email, media_limit)
            check_media(app_client, records, owner_email, user_id)
            summary["checked"] = len(records)
            try:
                summary["attendance"] = check_attendance(
                    app_client, owner_email, user_id, ATTENDANCE_CHECKS_PER_RUN)
            except Exception:
                db.session.rollback()
                logger.exception("Attendance check failed for %s", owner_email)

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
    if not BACKFILL_ENABLED:
        return False  # history before the first sync window is intentionally not fetched
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

def enrolled_users() -> list[str]:
    """
    Users whose meetings are synced: org accounts that signed in (or opened the Teams page)
    since meeting sync was enabled — each has a CalendarSync row. Users who only signed in
    before that are never synced in the background until they sign in again.
    ACTIVE_USER_DAYS > 0 additionally skips enrolled users who haven't signed in that recently.
    """
    return _enrolled(CalendarSync.query.all())


def due_users() -> list[str]:
    """
    Enrolled users whose last sync is older than SYNC_INTERVAL and whom no one is syncing now,
    in one query. The background loop only claims these (sync_user would skip the rest).
    """
    now = datetime.utcnow()
    return _enrolled(CalendarSync.query.filter(
        or_(CalendarSync.last_synced_at.is_(None), CalendarSync.last_synced_at < now - SYNC_INTERVAL),
        or_(CalendarSync.sync_started_at.is_(None),
            CalendarSync.sync_started_at < now - STALE_LOCK_AFTER),
    ).all())


def _enrolled(rows: list[CalendarSync]) -> list[str]:
    suffix = f"@{Config.ORG_DOMAIN}".lower()
    emails = [row.owner_email for row in rows if row.owner_email.endswith(suffix)]
    if ACTIVE_USER_DAYS > 0 and emails:
        since = datetime.utcnow() - timedelta(days=ACTIVE_USER_DAYS)
        recent = {u.email.lower() for u in User.query.filter(User.last_login >= since).all()}
        emails = [e for e in emails if e in recent]
    return emails


def is_enrolled(owner_email: str) -> bool:
    return get_sync_state((owner_email or "").lower()) is not None


def start_initial_sync(app, owner_email: str) -> None:
    """
    Called at sign-in: enrol the user and fetch their latest window in the background, so
    the Teams page is ready when they open it (never blocks the login redirect).
    """
    owner_email = (owner_email or "").lower()
    if not owner_email.endswith(f"@{Config.ORG_DOMAIN}".lower()):
        return

    def run():
        try:
            with app.app_context():
                sync_user(owner_email, min_age=MANUAL_SYNC_COOLDOWN, media_limit=0, backfill=False)
                db.session.remove()
        except Exception:
            logger.exception("Initial sync at sign-in failed for %s", owner_email)

    threading.Thread(target=run, name="meeting-sync-login", daemon=True).start()


def _loop(app) -> None:
    time.sleep(10)  # let the app finish starting
    while True:
        try:
            with app.app_context():
                for email in due_users():
                    sync_user(email)
                db.session.remove()
        except Exception:
            logger.exception("Background sync tick failed")
        time.sleep(LOOP_TICK_SECONDS)


_sync_all_lock = threading.Lock()
_sync_all_running = False


def start_sync_all(app) -> tuple[bool, int]:
    """
    Admin "Sync all users": sync every enrolled user in a background thread, one at a time
    (each still honours the per-user lock + 60 s cooldown). Returns (started, user_count);
    started is False if a sync-all is already running in this process.
    """
    global _sync_all_running
    with _sync_all_lock:
        if _sync_all_running:
            return False, 0
        _sync_all_running = True
    with app.app_context():
        emails = enrolled_users()

    def run():
        global _sync_all_running
        try:
            with app.app_context():
                for email in emails:
                    try:
                        sync_user(email, min_age=MANUAL_SYNC_COOLDOWN)
                    except Exception:
                        logger.exception("Sync-all failed for %s", email)
                db.session.remove()
        finally:
            _sync_all_running = False
            logger.info("Sync of all users finished (%d users)", len(emails))

    threading.Thread(target=run, name="meeting-sync-all", daemon=True).start()
    return True, len(emails)


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
