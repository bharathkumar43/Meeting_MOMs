import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from functools import wraps

logger = logging.getLogger(__name__)

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, session, flash, send_file, jsonify, Response, stream_with_context,
)
import io
from docx import Document
from werkzeug.utils import secure_filename

from app.auth import get_auth_url, process_auth_callback, get_token, get_app_token, logout as auth_logout
from app.graph_client import GraphClient
from app import db, meeting_sync
from app.meeting_sync import (
    candidate_user_ids as _app_user_ids_for_transcript_lookup,
    find_recording as _find_recording,
    lookup_meeting_media as _lookup_meeting_media,
    record_media, record_to_meeting,
)
from app.models import MeetingRecord
from app.zoom_auth import get_zoom_access_token
from app.zoom_client import ZoomClient
from app.meeting_filter import (
    filter_customer_meetings, filter_by_subject,
    parse_vtt_transcript, transcript_to_readable, transcript_view_lines,
)
from app.doc_generator import generate_mom_document, convert_docx_to_pdf
from app.email_sender import send_mom_email
from app.mom_generator import generate_mom_from_transcript
from app.activity_tracker import (
    record_login, record_meeting_access, record_mom_sent,
    get_all_users, get_user_stats, get_pending_moms, get_sent_moms,
    get_managers, get_non_managers,
    get_audit_rows, get_audit_totals,
    get_all_user_dashboard_stats, get_user_overview,
)
from app.audit_report_email import send_daily_audit_report
from config import Config

main_bp = Blueprint("main", __name__)


def _current_user_email() -> str:
    user = session.get("user", {})
    return (user.get("preferred_username") or user.get("upn") or "").lower()


@main_bp.app_context_processor
def inject_layout_context() -> dict:
    """Navbar profile info and date-filter presets shared by every page."""
    user = session.get("user") or {}
    name = user.get("name", "") or ""
    email = _current_user_email()
    initials = "".join(part[0] for part in name.split()[:2]).upper() or (email[:1].upper() or "U")
    today = datetime.now()
    return {
        "nav_user": {
            "name": name or email,
            "email": email,
            "initials": initials,
            "is_admin": bool(email) and email in Config.ADMIN_EMAILS,
        },
        "date_presets": {
            "today": today.strftime("%Y-%m-%d"),
            "presets": [
                ("Today", today.strftime("%Y-%m-%d")),
                ("7 days", (today - timedelta(days=7)).strftime("%Y-%m-%d")),
                ("30 days", (today - timedelta(days=30)).strftime("%Y-%m-%d")),
                ("90 days", (today - timedelta(days=90)).strftime("%Y-%m-%d")),
            ],
        },
    }


@main_bp.app_template_filter("nice_date")
def nice_date(value: str) -> str:
    """'2026-10-19T14:00:00' or '2026-10-19' -> 'Mon, 19 Oct 2026'."""
    try:
        return datetime.strptime((value or "")[:10], "%Y-%m-%d").strftime("%a, %d %b %Y")
    except ValueError:
        return value or ""


@main_bp.route("/favicon.ico")
def favicon():
    return Response(status=204)


TRANSCRIPT_UPLOAD_MAX_BYTES = 10 * 1024 * 1024
ALLOWED_TRANSCRIPT_SUFFIXES = {".txt", ".vtt", ".docx"}


def _decode_text_file(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _text_from_uploaded_vtt(raw: bytes) -> str:
    text = _decode_text_file(raw)
    entries = parse_vtt_transcript(text)
    if entries:
        return transcript_to_readable(entries).strip()
    return text.strip()


def _text_from_uploaded_docx(raw: bytes) -> str:
    doc = Document(io.BytesIO(raw))
    parts = []
    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if t:
            parts.append(t)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                t = (cell.text or "").strip()
                if t:
                    parts.append(t)
    return "\n".join(parts).strip()


def _match_recording(meeting, all_recordings):
    """Check if a calendar event has a matching recording file in OneDrive."""
    return _find_recording(meeting, all_recordings) is not None


def _graph_transcript_exists_app(app_client, join_url, user_ids):
    """True if any listed user can resolve join_url to an online meeting with transcripts."""
    if not join_url or not app_client or not user_ids:
        return False
    for uid in user_ids:
        if not uid:
            continue
        try:
            online_meeting = app_client.get_online_meeting_for_user(uid, join_url)
            if not online_meeting:
                continue
            meeting_id = online_meeting["id"]
            transcripts = app_client.list_transcripts(meeting_id, user_id=uid)
            if transcripts:
                return True
        except Exception as e:
            logger.debug("App transcript lookup failed for uid=%s: %s", uid, e)
            continue
    return False


def meeting_has_transcript_signal(
    meeting,
    all_recordings,
    delegated_client,
    app_client,
    signed_in_email="",
    viewed_user_id=None,
):
    """
    True if we expect transcript content to be available: OneDrive recording match
    OR Graph lists at least one transcript (delegated /me path, then app fallback).
    """
    if _match_recording(meeting, all_recordings):
        return True

    join_url = (meeting.get("onlineMeeting") or {}).get("joinUrl", "") or ""
    if not join_url:
        return False

    if delegated_client:
        try:
            if delegated_client.check_transcript_exists(join_url):
                return True
        except Exception as e:
            logger.debug("Delegated transcript check failed: %s", e)

    if app_client:
        ids = _app_user_ids_for_transcript_lookup(
            app_client, meeting, signed_in_email=signed_in_email, viewed_user_id=viewed_user_id
        )
        if _graph_transcript_exists_app(app_client, join_url, ids):
            return True

    return False


_RECORDING_MAP_LIMIT = 300


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_token()
        if not token:
            return redirect(url_for("main.login_page"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_token()
        if not token:
            return redirect(url_for("main.login_page"))
        user = session.get("user", {})
        email = (user.get("preferred_username") or user.get("upn") or "").lower()
        if email not in Config.ADMIN_EMAILS:
            flash("Access denied. Admin privileges required.", "danger")
            return redirect(url_for("main.dashboard"))
        return f(*args, **kwargs)
    return decorated


# ── Auth Routes ──────────────────────────────────────────────

@main_bp.route("/")
def index():
    if get_token():
        return redirect(url_for("main.dashboard"))
    return render_template("login.html")


@main_bp.route("/login")
def login():
    auth_url = get_auth_url()
    return redirect(auth_url)


@main_bp.route("/login-page")
def login_page():
    return render_template("login.html")


@main_bp.route("/auth/callback")
def auth_callback():
    result = process_auth_callback()
    if result:
        user = session.get("user", {})
        email = (user.get("preferred_username") or user.get("upn") or "").lower()
        name = user.get("name", "")
        if email:
            record_login(email, name)
        flash("Signed in successfully.", "success")
        return redirect(url_for("main.dashboard"))
    flash("Authentication failed. Please try again.", "danger")
    return redirect(url_for("main.login_page"))


@main_bp.route("/logout")
def logout():
    auth_logout()
    flash("You have been signed out.", "info")
    return redirect(url_for("main.index"))


@main_bp.route("/admin/grant-consent")
@admin_required
def admin_grant_consent():
    """Redirect admin to Azure AD admin consent page."""
    consent_url = (
        f"https://login.microsoftonline.com/{Config.AZURE_TENANT_ID}"
        f"/adminconsent?client_id={Config.AZURE_CLIENT_ID}"
        f"&redirect_uri={Config.REDIRECT_URI}"
    )
    return redirect(consent_url)


# ── Dashboard ────────────────────────────────────────────────

@main_bp.route("/dashboard")
@login_required
def dashboard():
    """Overview: the signed-in user's own MOM activity, read from the DB only."""
    overview = get_user_overview(_current_user_email())
    return render_template("dashboard.html", overview=overview)


# ── Teams Meetings ───────────────────────────────────────────

_TEAMS_PAGE_SIZE = 50


def _end_of_today_utc() -> datetime:
    now = datetime.utcnow()
    return datetime(now.year, now.month, now.day, 23, 59, 59)


def _teams_query(owner_email: str, keyword: str, start: datetime | None = None,
                 end: datetime | None = None):
    """Stored customer meetings for one user, newest first, optional range + subject terms."""
    q = MeetingRecord.query.filter(
        MeetingRecord.owner_email == owner_email,
        MeetingRecord.start_utc <= (end or _end_of_today_utc()),
    )
    if start:
        q = q.filter(MeetingRecord.start_utc >= start)
    terms = [t.strip() for t in (keyword or "").split(",") if t.strip()]
    if terms:
        def like(term: str):
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            return MeetingRecord.subject.ilike(f"%{escaped}%", escape="\\")
        q = q.filter(db.or_(*[like(t) for t in terms]))
    return q.order_by(MeetingRecord.start_utc.desc())


def _history_complete(state) -> bool:
    oldest = datetime.utcnow() - timedelta(days=meeting_sync.BACKFILL_DAYS)
    return bool(state and state.synced_from and state.synced_from <= oldest + timedelta(days=1))


@main_bp.app_template_filter("time_ago")
def time_ago(value: datetime | None) -> str:
    if not value:
        return "never"
    secs = int((datetime.utcnow() - value).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return value.strftime("%d %b %H:%M UTC")


@main_bp.route("/meetings/teams")
@login_required
def teams_meetings():
    """Teams customer meetings, read from the database (kept fresh by meeting_sync)."""
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    keyword = request.args.get("keyword", "")
    email = _current_user_email()

    state = meeting_sync.get_sync_state(email)
    if state is None or state.last_synced_at is None:
        # First visit: pull the latest month now (calendar only, ~2 s); transcript/video
        # status loads in the background on the page, history fills in as it's needed
        try:
            meeting_sync.sync_user(email, min_age=timedelta(0), media_limit=0, backfill=False)
        except Exception:
            logger.exception("Initial meeting sync failed for %s", email)
        state = meeting_sync.get_sync_state(email)
    if state and state.last_error:
        flash("Couldn't refresh meetings from Microsoft just now; showing the last synced data.",
              "warning")

    all_mode = not start_date and not end_date
    next_offset = None
    try:
        if all_mode:
            rows = _teams_query(email, keyword).limit(_TEAMS_PAGE_SIZE + 1).all()
            if len(rows) > _TEAMS_PAGE_SIZE or not _history_complete(state):
                next_offset = min(len(rows), _TEAMS_PAGE_SIZE)
            rows = rows[:_TEAMS_PAGE_SIZE]
        else:
            today = datetime.utcnow()
            start = datetime.strptime(
                start_date or (today - timedelta(days=7)).strftime("%Y-%m-%d"), "%Y-%m-%d"
            )
            end = datetime.strptime(end_date or today.strftime("%Y-%m-%d"), "%Y-%m-%d")
            start_date, end_date = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
            if state and state.synced_from and start < state.synced_from:
                meeting_sync.ensure_backfill(email, start)  # range older than stored history
            rows = _teams_query(
                email, keyword, start, end.replace(hour=23, minute=59, second=59)
            ).all()
    except ValueError:
        flash("Invalid date range.", "warning")
        return redirect(url_for("main.teams_meetings"))

    return render_template(
        "teams_meetings.html",
        meetings=[record_to_meeting(r) for r in rows],
        start_date="" if all_mode else start_date,
        end_date="" if all_mode else end_date,
        keyword=keyword,
        all_mode=all_mode,
        next_offset=next_offset,
        last_synced_at=state.last_synced_at if state else None,
    )


@main_bp.route("/meetings/teams/older")
@login_required
def teams_meetings_older():
    """Next page of the "All" view (from the database; backfills history when it runs out)."""
    keyword = request.args.get("keyword", "")
    try:
        offset = max(int(request.args.get("offset", "0")), 0)
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid offset."}), 400
    email = _current_user_email()

    try:
        rows = _teams_query(email, keyword).offset(offset).limit(_TEAMS_PAGE_SIZE + 1).all()
        state = meeting_sync.get_sync_state(email)
        # Ran out of stored meetings but history isn't complete: fetch older months now
        while len(rows) <= _TEAMS_PAGE_SIZE and not _history_complete(state):
            reached = state.synced_from if state and state.synced_from else datetime.utcnow()
            oldest = datetime.utcnow() - timedelta(days=meeting_sync.BACKFILL_DAYS)
            until = max(reached - timedelta(days=meeting_sync.BACKFILL_CHUNK_DAYS), oldest)
            meeting_sync.ensure_backfill(email, until)
            state = meeting_sync.get_sync_state(email)
            rows = _teams_query(email, keyword).offset(offset).limit(_TEAMS_PAGE_SIZE + 1).all()
    except Exception:
        db.session.rollback()
        logger.exception("teams_meetings_older failed")
        return jsonify({"status": "error", "message": "Could not load older meetings."}), 502

    has_more = len(rows) > _TEAMS_PAGE_SIZE
    rows = rows[:_TEAMS_PAGE_SIZE]
    return jsonify({"status": "ok", "data": {
        "html": render_template(
            "_teams_rows.html", meetings=[record_to_meeting(r) for r in rows],
            back_url=url_for("main.teams_meetings"),
        ),
        "count": len(rows),
        "next_offset": offset + len(rows) if has_more else None,
    }}), 200


@main_bp.route("/meetings/teams/status", methods=["POST"])
@login_required
def teams_meetings_status():
    """Transcript/video status for the user's own stored meetings; checks unchecked ones now."""
    if request.method != "POST" or not request.is_json:
        return jsonify({"status": "error", "message": "Expected JSON."}), 400
    payload = request.get_json(silent=True, force=True) or {}
    ids = [i for i in payload.get("ids", []) if isinstance(i, str)][:25]
    email = _current_user_email()
    if not ids:
        return jsonify({"status": "ok", "data": {}}), 200

    # Only the signed-in user's own meetings (owner_email filter) are ever looked up
    records = MeetingRecord.query.filter(
        MeetingRecord.owner_email == email, MeetingRecord.event_id.in_(ids)
    ).all()
    unchecked = [r for r in records if r.has_transcript is None]
    if unchecked:
        app_token = get_app_token()
        if not app_token:
            return jsonify({"status": "error", "message": "Status temporarily unavailable."}), 503
        try:
            app_client = GraphClient(app_token)
            meeting_sync.check_media(
                app_client, unchecked, email, app_client._resolve_user_id_safe(email) or ""
            )
        except Exception:
            db.session.rollback()
            logger.exception("teams_meetings_status failed")
            return jsonify({"status": "error", "message": "Could not check meetings."}), 502

    return jsonify({"status": "ok", "data": {
        r.event_id: {"transcript": bool(r.has_transcript), "video": bool(r.has_recording)}
        for r in records if r.has_transcript is not None
    }}), 200


@main_bp.route("/meetings/teams/sync", methods=["POST"])
@login_required
def teams_meetings_sync():
    """Sync button: re-read the calendar now and re-check recent meetings (60 s cooldown)."""
    if request.method != "POST":
        return jsonify({"status": "error", "message": "POST required."}), 405
    email = _current_user_email()
    result = meeting_sync.sync_user(
        email, min_age=meeting_sync.MANUAL_SYNC_COOLDOWN, media_limit=0,
        backfill=False, recheck_recent=True,
    )
    if "skipped" in result:
        return jsonify({"status": "ok", "data": {
            "skipped": True, "message": "Already up to date (synced less than a minute ago).",
        }}), 200
    if "error" in result:
        return jsonify({"status": "error", "message": "Sync failed. Please try again."}), 502
    return jsonify({"status": "ok", "data": result}), 200


# ── Transcript View ──────────────────────────────────────────

@main_bp.route("/transcript")
@login_required
def transcript():
    event_id = request.args.get("event_id", "")
    join_url = request.args.get("join_url", "")
    back_url = request.args.get("back") or url_for("main.teams_meetings")

    token = get_token()
    client = GraphClient(token)

    # Fetch the calendar event directly by ID (avoids loading 90 days of events)
    try:
        meeting = client.get_event(event_id)
    except Exception:
        meeting = None

    if not meeting:
        flash("Meeting not found.", "warning")
        return redirect(url_for("main.teams_meetings"))

    # Build attendees list
    attendees = []
    for att in meeting.get("attendees", []):
        attendees.append({
            "name": att.get("emailAddress", {}).get("name", ""),
            "email": att.get("emailAddress", {}).get("address", ""),
        })

    transcript_text = ""

    transcript_lines = []
    error = None  # kept for template compatibility; transcript fetch warnings are not shown
    organizer_email = (
        meeting.get("organizer", {}).get("emailAddress", {}).get("address", "")
    )

    # Use the join URL from the calendar event directly (avoids URL encoding issues)
    meeting_join_url = (
        meeting.get("onlineMeeting", {}).get("joinUrl", "")
    )
    effective_join_url = meeting_join_url or join_url

    logger.info(
        "Transcript request: subject='%s', organizer='%s', "
        "join_url_param=%d chars, meeting_join_url=%d chars",
        meeting.get("subject", ""),
        organizer_email,
        len(join_url),
        len(meeting_join_url),
    )

    # Step 0: the stored record already knows this meeting's owner + onlineMeeting ID —
    # fetch the transcript directly instead of repeating the lookups below.
    stored = meeting_sync.get_record(_current_user_email(), meeting["id"])
    cached_media = record_media(stored) if stored and stored.has_transcript is not None else None
    tried_app_lookup = False
    if cached_media is None and effective_join_url:
        # One shared lookup (owner, onlineMeeting, transcript, recording) for this page
        app_token0 = get_app_token()
        if app_token0:
            try:
                lookup_client = GraphClient(app_token0)
                ids0 = _app_user_ids_for_transcript_lookup(
                    lookup_client, meeting, signed_in_email=_current_user_email()
                )
                cached_media = _lookup_meeting_media(lookup_client, meeting, ids0)
                tried_app_lookup = True
                if stored is not None and cached_media.get("online_meeting_id"):
                    meeting_sync.save_media(stored, cached_media)
                    db.session.commit()
            except Exception as e:
                db.session.rollback()
                logger.warning("Step 0 lookup failed: %s", e)
    skip_lookups = False
    if cached_media and cached_media.get("online_meeting_id"):
        skip_lookups = not cached_media["has_transcript"]
        if cached_media["has_transcript"]:
            try:
                app_client0 = GraphClient(get_app_token())
                uid0, mid0 = cached_media["user_id"], cached_media["online_meeting_id"]
                transcripts = app_client0.list_transcripts(mid0, user_id=uid0)
                if transcripts:
                    vtt_content = app_client0.get_transcript_content(
                        mid0, transcripts[0]["id"], user_id=uid0
                    )
                    if vtt_content:
                        entries = parse_vtt_transcript(vtt_content)
                        transcript_text = transcript_to_readable(entries)
                        transcript_lines = transcript_view_lines(entries)
                        logger.info("Step 0 SUCCESS (cached lookup): %d chars", len(transcript_text))
            except Exception as e:
                logger.warning("Step 0 FAILED: %s", e)
    if skip_lookups:
        logger.info("Step 0: cached lookup says no transcript; skipping Steps 1-3")
        transcript_text = transcript_text or ""

    # Step 1: Try delegated token (works when user has OnlineMeetings.Read consent)
    try:
        online_meeting = (
            None if (transcript_text or skip_lookups)
            else client.get_online_meeting_by_join_url(effective_join_url)
        )
        if online_meeting:
            meeting_id = online_meeting["id"]
            logger.info("Step 1 OK: Found online meeting via delegated token")
            transcripts = client.list_transcripts(meeting_id)
            logger.info("Step 1: %d transcript(s) found", len(transcripts))
            if transcripts:
                transcript_id = transcripts[0]["id"]
                vtt_content = client.get_transcript_content(meeting_id, transcript_id)
                if vtt_content:
                    entries = parse_vtt_transcript(vtt_content)
                    transcript_text = transcript_to_readable(entries)
                    transcript_lines = transcript_view_lines(entries)
                    logger.info("Step 1 SUCCESS: loaded %d chars", len(transcript_text))
        else:
            logger.info("Step 1: No online meeting found via delegated token")
    except Exception as e:
        logger.warning("Step 1 FAILED: %s", e)

    # Step 2: If delegated didn't work, try app token (Step 0 already tried the same users)
    if not transcript_text and not skip_lookups and not tried_app_lookup:
        app_token = get_app_token()
        if app_token and effective_join_url:
            app_client = GraphClient(app_token)

            user_data = session.get("user", {})
            signed_in_email = (
                user_data.get("preferred_username") or user_data.get("upn") or ""
            )
            targets = []
            link_oid = GraphClient.organizer_id_from_join_url(effective_join_url)
            if link_oid:
                targets.append(("join-link organizer", link_oid))
            if organizer_email:
                targets.append(("organizer", organizer_email))
            if signed_in_email and signed_in_email.lower() != (organizer_email or "").lower():
                targets.append(("signed-in user", signed_in_email))

            tried_uids = set()
            for label, target in targets:
                if transcript_text:
                    break
                try:
                    uid = target if label == "join-link organizer" else (
                        app_client._resolve_user_id_safe(target)
                    )
                    if not uid:
                        logger.info("Step 2: Cannot resolve %s (%s)", label, target)
                        continue
                    if uid in tried_uids:
                        continue
                    tried_uids.add(uid)
                    logger.info("Step 2: Trying via %s (%s)", label, target)
                    online_meeting = app_client.get_online_meeting_for_user(
                        uid, effective_join_url
                    )
                    if not online_meeting:
                        logger.info("Step 2: No meeting found via %s", label)
                        continue
                    meeting_id = online_meeting["id"]
                    logger.info("Step 2: Found meeting via %s", label)
                    transcripts = app_client.list_transcripts(meeting_id, user_id=uid)
                    logger.info("Step 2: %d transcript(s) via %s", len(transcripts), label)
                    if transcripts:
                        transcript_id = transcripts[0]["id"]
                        vtt_content = app_client.get_transcript_content(
                            meeting_id, transcript_id, user_id=uid
                        )
                        if vtt_content:
                            entries = parse_vtt_transcript(vtt_content)
                            transcript_text = transcript_to_readable(entries)
                            transcript_lines = transcript_view_lines(entries)
                            logger.info("Step 2 SUCCESS via %s: %d chars", label, len(transcript_text))
                except Exception as e:
                    logger.warning("Step 2 FAILED via %s: %s", label, e)

    # Step 3: Search OneDrive directly for the Teams transcript VTT/DOCX file.
    # This fires when both API steps fail (common when app access policy isn't configured).
    # Teams saves transcript files alongside the recording in the organizer's OneDrive.
    step3_deadline = time.monotonic() + 10  # never wait out long Graph throttling here
    if not transcript_text and not skip_lookups:
        step3_token = get_app_token()
        if step3_token:
            step3_client = GraphClient(step3_token)
            meeting_date_8 = (
                meeting.get("start", {}).get("dateTime", "")[:10].replace("-", "")
            )
            meeting_subject = meeting.get("subject", "")

            user_s3 = session.get("user", {})
            signed_in_s3 = (
                user_s3.get("preferred_username") or user_s3.get("upn") or ""
            )
            step3_targets = []
            if organizer_email:
                step3_targets.append(organizer_email)
            if signed_in_s3 and signed_in_s3.lower() != (organizer_email or "").lower():
                step3_targets.append(signed_in_s3)

            step3_oid = GraphClient.organizer_id_from_join_url(effective_join_url)
            if step3_oid:
                step3_targets.insert(0, step3_oid)

            for target_email in step3_targets:
                if transcript_text:
                    break
                uid = target_email if target_email == step3_oid else (
                    step3_client._resolve_user_id_safe(target_email)
                )
                if not uid:
                    logger.info("Step 3: Cannot resolve user %s", target_email)
                    continue
                logger.info("Step 3: Searching OneDrive transcripts for %s", target_email)
                found_files = step3_client._search_user_transcripts(uid, step3_deadline)
                for tr in found_files:
                    if tr["date"] != meeting_date_8:
                        continue
                    if not _subject_match(tr["subject"], meeting_subject):
                        continue
                    logger.info(
                        "Step 3: Matched '%s' (%s), downloading item %s",
                        tr["subject"], tr["ext"], tr["item_id"],
                    )
                    raw = step3_client.get_onedrive_file_content(uid, tr["item_id"])
                    if not raw:
                        continue
                    if tr["ext"] == "vtt":
                        content = _decode_bytes(raw)
                        entries = parse_vtt_transcript(content)
                        transcript_text = transcript_to_readable(entries)
                        transcript_lines = transcript_view_lines(entries)
                    elif tr["ext"] == "docx":
                        try:
                            doc = Document(io.BytesIO(raw))
                            transcript_text = "\n".join(
                                p.text for p in doc.paragraphs if p.text.strip()
                            )
                        except Exception as docx_err:
                            logger.warning("Step 3: DOCX parse error: %s", docx_err)
                    if transcript_text:
                        logger.info(
                            "Step 3 SUCCESS via OneDrive for %s: %d chars",
                            target_email, len(transcript_text),
                        )
                        break

    user = session.get("user", {})
    email = (user.get("preferred_username") or user.get("upn") or "").lower()
    if email:
        record_meeting_access(
            email,
            meeting.get("subject", ""),
            meeting.get("start", {}).get("dateTime", "")[:10],
        )

    recording = None
    app_token_rec = get_app_token()
    if app_token_rec:
        recording = _recording_for_meeting(meeting, GraphClient(app_token_rec))

    return render_template(
        "transcript.html",
        meeting=meeting,
        transcript_text=transcript_text,
        transcript_lines=transcript_lines,
        recording=recording,
        attendees_json=json.dumps(attendees),
        error=error,
        back_url=back_url,
        back_label="Teams Meetings",
    )


def _recording_for_meeting(meeting, app_client) -> dict | None:
    """
    Playable recording for a calendar event the signed-in user owns, or None.
    Uses the stored record's lookup (the user's own MeetingRecord), else the recordings API.
    Only recordings matched to this event are ever served (never a client-supplied ID).
    """
    recording_map = dict(session.get("recording_map", {}))
    stored = meeting_sync.get_record(_current_user_email(), meeting["id"])
    media = record_media(stored) if stored and stored.has_transcript is not None else None
    entry = None
    if media is not None:
        # Already looked up and stored — reuse, including "no recording"
        entry = media.get("recording")
        if entry:
            recording_map[meeting["id"]] = entry
            session["recording_map"] = dict(list(recording_map.items())[-_RECORDING_MAP_LIMIT:])
    elif recording_map.get(meeting["id"]):
        entry = recording_map[meeting["id"]]
    else:
        try:
            ids = _app_user_ids_for_transcript_lookup(
                app_client, meeting, signed_in_email=_current_user_email()
            )
            entry = _lookup_meeting_media(app_client, meeting, ids)["recording"]
        except Exception:
            logger.exception("Recording lookup failed")
            entry = None
        if entry:
            recording_map[meeting["id"]] = entry
            session["recording_map"] = recording_map

    if not entry:
        return None
    if entry.get("kind") == "api":
        stream_url = url_for("main.teams_recording_stream", event_id=meeting["id"])
        return {"src": stream_url, "download_url": f"{stream_url}&download=1", "web_url": ""}
    file_info = app_client.get_recording_file(entry["user_id"], entry["item_id"])
    if not file_info or not file_info.get("download_url"):
        return None
    return {
        "src": file_info["download_url"],
        "download_url": file_info["download_url"],
        "web_url": file_info.get("web_url", ""),
    }


@main_bp.route("/meetings/teams/recording")
@login_required
def teams_recording():
    """Old video-only page: the meeting page now shows video and transcript together."""
    event_id = request.args.get("event_id", "")
    return redirect(url_for("main.transcript", event_id=event_id))


@main_bp.route("/meetings/teams/recording/stream")
@login_required
def teams_recording_stream():
    """
    Proxy a recordings-API video to the browser (Graph has no pre-authenticated URL for
    these). Only serves entries the signed-in user's own calendar lookup put in the session.
    """
    event_id = request.args.get("event_id", "")
    entry = session.get("recording_map", {}).get(event_id)
    if not entry or entry.get("kind") != "api":
        return Response("Recording not available.", status=404)

    app_token = get_app_token()
    if not app_token:
        return Response("Recording temporarily unavailable.", status=503)

    upstream = GraphClient(app_token).open_recording_stream(
        entry["user_id"], entry["meeting_id"], entry["recording_id"],
        range_header=request.headers.get("Range"),
    )
    if upstream is None:
        return Response("Recording could not be loaded.", status=502)

    headers = {
        k: upstream.headers[k]
        for k in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")
        if k in upstream.headers
    }
    headers.setdefault("Content-Type", "video/mp4")
    headers.setdefault("Accept-Ranges", "bytes")
    if request.args.get("download") == "1":
        headers["Content-Disposition"] = 'attachment; filename="meeting-recording.mp4"'

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(stream_with_context(generate()), status=upstream.status_code, headers=headers)


@main_bp.route("/transcript/parse-file", methods=["POST"])
@login_required
def parse_transcript_file():
    """Extract plain text from an uploaded .txt, .vtt, or .docx transcript file."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
    upload = request.files["file"]
    if not upload or not upload.filename:
        return jsonify({"error": "No file selected."}), 400

    safe_name = secure_filename(upload.filename)
    _, ext = os.path.splitext(safe_name.lower())
    if ext not in ALLOWED_TRANSCRIPT_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_TRANSCRIPT_SUFFIXES))
        return jsonify({"error": f"Unsupported type. Allowed: {allowed}"}), 400

    raw = upload.read(TRANSCRIPT_UPLOAD_MAX_BYTES + 1)
    if len(raw) > TRANSCRIPT_UPLOAD_MAX_BYTES:
        return jsonify({"error": "File is too large (maximum 10 MB)."}), 413

    try:
        if ext == ".txt":
            text = _decode_text_file(raw).strip()
        elif ext == ".vtt":
            text = _text_from_uploaded_vtt(raw)
        else:
            text = _text_from_uploaded_docx(raw)
    except Exception as e:
        logger.exception("parse_transcript_file failed")
        return jsonify({"error": f"Could not read this file: {str(e)}"}), 400

    if not text or not text.strip():
        return jsonify({"error": "No text could be extracted from this file."}), 400

    return jsonify({"text": text})


# ── MOM Builder ──────────────────────────────────────────────

@main_bp.route("/mom-builder", methods=["POST"])
@login_required
def mom_builder():
    meeting_subject = request.form.get("meeting_subject", "")
    meeting_date = request.form.get("meeting_date", "")
    meeting_time = request.form.get("meeting_time", "")
    attendees_json = request.form.get("attendees_json", "[]")
    transcript = request.form.get("transcript", "")

    mom, gen_error = generate_mom_from_transcript(transcript, meeting_subject)
    if gen_error:
        flash(f"AI generation: {gen_error}", "warning")

    return render_template(
        "mom_builder.html",
        meeting_subject=meeting_subject,
        meeting_date=meeting_date,
        meeting_time=meeting_time,
        attendees_json=attendees_json,
        transcript=transcript,
        ai_tldr=mom["tldr"],
        ai_action_items=mom["action_items"],
        ai_decisions=mom["decisions"],
    )


# ── Send Page ────────────────────────────────────────────────

@main_bp.route("/send", methods=["POST"])
@login_required
def send_page():
    meeting_subject = request.form.get("meeting_subject", "")
    meeting_date = request.form.get("meeting_date", "")
    meeting_time = request.form.get("meeting_time", "")
    attendees_json = request.form.get("attendees_json", "[]")
    transcript = request.form.get("transcript", "")

    title = request.form.get("title", meeting_subject)
    tldr = [p for p in request.form.getlist("tldr") if p.strip()]

    action_descs = request.form.getlist("action_desc")
    action_assignees = request.form.getlist("action_assignee")
    action_dues = request.form.getlist("action_due")
    action_items = []
    for desc, assignee, due in zip(action_descs, action_assignees, action_dues):
        if desc.strip():
            action_items.append({
                "description": desc,
                "assigned_to": assignee,
                "due_date": due,
            })

    decisions = request.form.getlist("decisions")
    decisions = [d for d in decisions if d.strip()]

    include_transcript = request.form.get("include_transcript") == "on"

    attendees = json.loads(attendees_json)

    doc_bytes = generate_mom_document(
        meeting_title=title,
        meeting_date=meeting_date,
        meeting_time=meeting_time,
        tldr=tldr,
        action_items=action_items,
        decisions=decisions,
        transcript_text=transcript if include_transcript else "",
    )

    # Extract external attendee emails for pre-filling
    org_domain = Config.ORG_DOMAIN.lower()
    external_attendees = [
        a for a in attendees
        if a.get("email") and not a["email"].lower().endswith(f"@{org_domain}")
    ]
    external_emails = [a["email"] for a in external_attendees]
    primary_email = external_emails[0] if external_emails else ""

    # Resolve greeting name from the local part of the primary external email
    # e.g. john.doe@example.com → "John"
    if primary_email:
        local_part = primary_email.split("@")[0]
        first_token = re.split(r"[._\-]", local_part)[0]
        greeting_name = first_token.capitalize() if first_token else "Customer"
    else:
        greeting_name = "Customer"

    # Build CC list: remaining external attendees + default team addresses
    cc_parts = external_emails[1:]
    for default_addr in [Config.MOM_DEFAULT_DL_EMAIL, Config.MOM_DEFAULT_CC_EMAIL]:
        if default_addr and default_addr not in cc_parts:
            cc_parts.append(default_addr)
    prefill_cc = ", ".join(cc_parts)

    has_default_cc = bool(Config.MOM_DEFAULT_DL_EMAIL or Config.MOM_DEFAULT_CC_EMAIL)

    session["mom_doc"] = {
        "bytes_hex": doc_bytes.hex(),
        "meeting_subject": title,
        "meeting_date": meeting_date,
        "meeting_time": meeting_time,
        "attendees": attendees,
    }

    return render_template(
        "send.html",
        meeting_subject=title,
        meeting_date=meeting_date,
        meeting_time=meeting_time,
        action_count=len(action_items),
        decision_count=len(decisions),
        sent_success=False,
        prefill_email=primary_email,
        prefill_cc=prefill_cc,
        has_default_cc=has_default_cc,
        greeting_name=greeting_name,
    )


# ── Download MOM ─────────────────────────────────────────────

@main_bp.route("/download-mom")
@login_required
def download_mom():
    mom_data = session.get("mom_doc")
    if not mom_data:
        flash("No document available to download. Please generate a MOM first.", "warning")
        return redirect(url_for("main.dashboard"))

    doc_bytes = bytes.fromhex(mom_data["bytes_hex"])
    safe_title = "".join(
        c if c.isalnum() or c in " -_" else "_"
        for c in mom_data["meeting_subject"]
    )
    filename = f"MOM_{safe_title}_{mom_data['meeting_date']}.docx"

    return send_file(
        io.BytesIO(doc_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        as_attachment=True,
        download_name=filename,
    )


# ── Send Email ───────────────────────────────────────────────

@main_bp.route("/send-email", methods=["POST"])
@login_required
def send_email():
    mom_data = session.get("mom_doc")
    if not mom_data:
        flash("No document available. Please generate a MOM first.", "warning")
        return redirect(url_for("main.dashboard"))

    to_email = request.form.get("to_email", "").strip()
    cc_emails = request.form.get("cc_emails", "").strip()
    format_choice = request.form.get("format_choice", "docx")  # "docx" | "pdf"
    greeting_name = request.form.get("greeting_name", "").strip() or "Customer"

    to_list = [e.strip() for e in to_email.split(",") if e.strip()]
    cc_list = [e.strip() for e in cc_emails.split(",") if e.strip()]

    if not to_list:
        flash("Please provide at least one customer email address.", "warning")
        return redirect(url_for("main.send_page"))

    try:
        token = get_token()
        doc_bytes = bytes.fromhex(mom_data["bytes_hex"])

        safe_title = "".join(
            c if c.isalnum() or c in " -_" else "_"
            for c in mom_data["meeting_subject"]
        )
        base_name = f"MOM_{safe_title}_{mom_data['meeting_date']}"

        # Build MOM attachment based on format selection
        attachments = []
        if format_choice == "pdf":
            pdf_bytes = convert_docx_to_pdf(doc_bytes)
            attachments.append({
                "bytes": pdf_bytes,
                "filename": f"{base_name}.pdf",
                "content_type": "application/pdf",
            })
        else:
            attachments.append({
                "bytes": doc_bytes,
                "filename": f"{base_name}.docx",
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            })

        # Append any extra file the user uploaded
        extra_file = request.files.get("extra_attachment")
        if extra_file and extra_file.filename:
            extra_bytes = extra_file.read()
            max_bytes = 20 * 1024 * 1024  # 20 MB
            if len(extra_bytes) > max_bytes:
                flash("Extra attachment exceeds the 20 MB size limit.", "warning")
                return redirect(url_for("main.send_page"))
            safe_extra_name = secure_filename(extra_file.filename)
            attachments.append({
                "bytes": extra_bytes,
                "filename": safe_extra_name,
                "content_type": extra_file.content_type or "application/octet-stream",
            })

        filename, sent_list = send_mom_email(
            access_token=token,
            to_emails=to_list,
            cc_emails=cc_list,
            meeting_title=mom_data["meeting_subject"],
            meeting_date=mom_data["meeting_date"],
            attachments=attachments,
            greeting_name=greeting_name,
        )

        user = session.get("user", {})
        email = (user.get("preferred_username") or user.get("upn") or "").lower()
        if email:
            for recipient in sent_list:
                record_mom_sent(
                    email=email,
                    subject=mom_data["meeting_subject"],
                    meeting_date=mom_data["meeting_date"],
                    sent_to=recipient.strip(),
                )

        sent_display = ", ".join(sent_list)
        flash(f"MOM sent successfully to {sent_display}!", "success")

        return render_template(
            "send.html",
            meeting_subject=mom_data["meeting_subject"],
            meeting_date=mom_data["meeting_date"],
            meeting_time=mom_data["meeting_time"],
            action_count=0,
            decision_count=0,
            sent_success=True,
            sent_to=sent_display,
        )

    except Exception as e:
        flash(f"Failed to send email: {str(e)}", "danger")
        return render_template(
            "send.html",
            meeting_subject=mom_data["meeting_subject"],
            meeting_date=mom_data["meeting_date"],
            meeting_time=mom_data["meeting_time"],
            action_count=0,
            decision_count=0,
            sent_success=False,
        )


# ── Admin Dashboard ───────────────────────────────────────────

@main_bp.route("/admin")
@admin_required
def admin_dashboard():
    users = get_all_users()
    managers = get_managers()
    non_managers = get_non_managers()
    total_users, total_meetings, total_sent = get_user_stats()
    pending_moms = get_pending_moms()
    sent_moms = get_sent_moms()
    user_dashboard_stats = get_all_user_dashboard_stats()

    manager_emails = Config.MANAGER_EMAILS
    manager_pending = [p for p in pending_moms if p.user_email in manager_emails]
    manager_sent = [s for s in sent_moms if s.user_email in manager_emails]

    audit_days = max(1, Config.AUDIT_REPORT_DAYS)
    audit_rows = get_audit_rows(days=audit_days)
    audit_totals = get_audit_totals(audit_rows)
    audit_can_send = bool(
        Config.AUDIT_SENDER_MAILBOX and Config.AUDIT_REPORT_RECIPIENTS
    )
    audit_sender_display = Config.AUDIT_SENDER_MAILBOX or "—"
    audit_recipients_display = ", ".join(Config.AUDIT_REPORT_RECIPIENTS) or "—"

    return render_template(
        "admin.html",
        users=users,
        managers=managers,
        non_managers=non_managers,
        total_users=total_users,
        total_meetings=total_meetings,
        total_sent=total_sent,
        total_pending=len(pending_moms),
        pending_moms=pending_moms,
        sent_moms=sent_moms,
        manager_pending=manager_pending,
        manager_sent=manager_sent,
        audit_rows=audit_rows,
        audit_totals=audit_totals,
        audit_days=audit_days,
        audit_can_send=audit_can_send,
        audit_sender_display=audit_sender_display,
        audit_recipients_display=audit_recipients_display,
        user_dashboard_stats=user_dashboard_stats,
    )


@main_bp.route("/admin/send-audit-report", methods=["POST"])
@admin_required
def admin_send_audit_report():
    """Send the rolling audit email now (Graph app-only)."""
    ok, msg = send_daily_audit_report(force=True)
    if ok:
        flash(msg, "success")
    else:
        flash(msg, "danger")
    return redirect(url_for("main.admin_dashboard") + "#audit-report")


# ── Admin: Per-User Meetings View ────────────────────────────

@main_bp.route("/admin/user-meetings/<path:user_email>")
@admin_required
def admin_user_meetings(user_email):
    today = datetime.now().strftime("%Y-%m-%d")
    default_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    start_date = request.args.get("start_date") or default_start
    end_date = request.args.get("end_date") or today

    meetings = []
    user_display_name = user_email
    error = None

    try:
        app_token = get_app_token()
        if not app_token:
            error = (
                "Application token not available. "
                "Ensure Application permissions (Calendars.Read, OnlineMeetingTranscript.Read.All) "
                "are configured in Azure AD with admin consent."
            )
        else:
            client = GraphClient(app_token)

            user_info = client.get_user_id(user_email)
            user_id = user_info["id"]
            user_display_name = user_info.get("displayName", user_email)

            events = client.list_user_calendar_events(user_id, start_date, end_date)
            meetings = filter_customer_meetings(events, Config.ORG_DOMAIN)

            all_recordings = client.build_recording_lookup(
                meetings, user_id, Config.ORG_DOMAIN
            )

            for meeting in meetings:
                meeting["has_transcript"] = _match_recording(meeting, all_recordings)

    except Exception:
        logger.exception("admin_user_meetings fetch failed for %s", user_email)
        error = (
            f"Couldn't load Microsoft meetings for {user_email}. The account may not exist "
            "in this Microsoft 365 tenant, or the app lacks permission to read its calendar."
        )
        meetings = []

    from app.models import MOMSent, User as UserModel
    sent_keys = set()
    db_user = UserModel.query.filter_by(email=user_email.lower()).first()
    if db_user:
        sent_records = db_user.sent_moms.all()
        for s in sent_records:
            sent_keys.add(f"{s.subject}|{s.meeting_date}")

    return render_template(
        "admin_user_meetings.html",
        user_email=user_email,
        user_display_name=user_display_name,
        meetings=meetings,
        sent_keys=sent_keys,
        start_date=start_date,
        end_date=end_date,
        error=error,
    )


# ── Zoom Dashboard ────────────────────────────────────────────

@main_bp.route("/zoom/dashboard")
@login_required
def zoom_dashboard():
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    keyword = request.args.get("keyword", "")

    explicitly_searched = bool(request.args.get("start_date") or request.args.get("end_date"))

    if not start_date:
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    user_data = session.get("user", {})
    user_email = (user_data.get("preferred_username") or user_data.get("upn") or "").lower()

    zoom_token = get_zoom_access_token()
    zoom_configured = bool(zoom_token)

    meetings = None
    if explicitly_searched and zoom_token and user_email:
        try:
            zoom = ZoomClient(zoom_token)
            recordings = zoom.list_user_recordings(user_email, start_date, end_date)
            meetings = [ZoomClient.normalize_recording(r) for r in recordings]

            if keyword:
                keywords = [k.strip() for k in keyword.split(",")]
                meetings = filter_by_subject(meetings, keywords)

        except Exception as e:
            flash(f"Error fetching Zoom recordings: {str(e)}", "danger")
            meetings = []

    return render_template(
        "zoom_dashboard.html",
        meetings=meetings,
        start_date=start_date,
        end_date=end_date,
        keyword=keyword,
        zoom_configured=zoom_configured,
    )


# ── Zoom Transcript View ──────────────────────────────────────

@main_bp.route("/zoom/transcript")
@login_required
def zoom_transcript():
    meeting_id = request.args.get("meeting_id", "")
    if not meeting_id:
        flash("No meeting ID provided.", "warning")
        return redirect(url_for("main.zoom_dashboard"))

    zoom_token = get_zoom_access_token()
    if not zoom_token:
        flash("Zoom integration is not configured. Add Zoom credentials to your .env file.", "danger")
        return redirect(url_for("main.zoom_dashboard"))

    zoom = ZoomClient(zoom_token)

    recording = zoom.get_meeting_recordings(meeting_id)
    if not recording:
        flash("Could not retrieve Zoom meeting recording.", "warning")
        return redirect(url_for("main.zoom_dashboard"))

    meeting = ZoomClient.normalize_recording(recording)

    transcript_text = ""

    transcript_lines = []
    if meeting.get("zoom_transcript_url"):
        try:
            vtt_content = zoom.get_transcript_content(meeting["zoom_transcript_url"])
            if vtt_content:
                entries = parse_vtt_transcript(vtt_content)
                transcript_text = transcript_to_readable(entries)
                transcript_lines = transcript_view_lines(entries)
                logger.info("Zoom transcript loaded: %d chars for meeting %s", len(transcript_text), meeting_id)
        except Exception as e:
            logger.warning("Zoom transcript fetch failed for %s: %s", meeting_id, e)

    attendees = []
    external_attendees = []
    try:
        participants = zoom.get_meeting_participants(meeting_id)
        org_domain = Config.ORG_DOMAIN.lower()
        for p in participants:
            email = (p.get("user_email") or "").strip()
            name = (p.get("name") or p.get("user_name") or "").strip()
            if email:
                attendees.append({"name": name, "email": email})
                if not email.lower().endswith(f"@{org_domain}"):
                    external_attendees.append({"name": name, "email": email})
    except Exception as e:
        logger.debug("Zoom participant lookup failed for transcript view %s: %s", meeting_id, e)

    meeting["attendees"] = attendees
    meeting["external_attendees"] = external_attendees

    user = session.get("user", {})
    email = (user.get("preferred_username") or user.get("upn") or "").lower()
    if email:
        record_meeting_access(
            email,
            meeting.get("subject", ""),
            meeting.get("start", {}).get("dateTime", "")[:10],
        )

    return render_template(
        "transcript.html",
        meeting=meeting,
        transcript_text=transcript_text,
        transcript_lines=transcript_lines,
        attendees_json=json.dumps(attendees),
        error=None,
        back_url=url_for("main.zoom_dashboard"),
        back_label="Zoom Meetings",
    )


# ── Google Meetings (calendar-based list + manual transcript upload) ──────────


def _subject_match(file_subject: str, meeting_subject: str) -> bool:
    """
    Fuzzy-match an OneDrive filename subject to a calendar meeting subject.
    Teams strips special chars (|, :, /) from filenames, so exact match rarely works.
    """
    def _norm(s: str) -> str:
        s = re.sub(r"[^a-z0-9\s]", " ", s.lower())
        return re.sub(r"\s+", " ", s).strip()

    a = _norm(file_subject)
    b = _norm(meeting_subject)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    words_a = set(a.split())
    words_b = set(b.split())
    common = words_a & words_b
    shorter = min(len(words_a), len(words_b))
    return shorter > 0 and len(common) / shorter >= 0.6


def _decode_bytes(raw: bytes) -> str:
    """Decode bytes trying common encodings in order."""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _parse_google_meet_transcript(html_body: str) -> str:
    """Strip HTML from a Google Meet transcript email and return plain 'Speaker: text' lines."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_body, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|tr|td|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = (
        text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        .replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    )
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return "\n".join(lines)


def _normalize_google_calendar_event(event: dict) -> dict:
    """Convert an Outlook calendar event (with Google Meet link) to the standard meeting dict."""
    from config import Config as _Config
    org_domain = (_Config.ORG_DOMAIN or "").lower()

    attendees_raw = event.get("attendees") or []
    attendees = [
        {
            "emailAddress": {
                "name": a.get("emailAddress", {}).get("name", ""),
                "address": a.get("emailAddress", {}).get("address", ""),
            }
        }
        for a in attendees_raw
        if (a.get("emailAddress") or {}).get("address")
    ]
    external_attendees = [
        {"name": a["emailAddress"]["name"], "email": a["emailAddress"]["address"]}
        for a in attendees
        if org_domain and not a["emailAddress"]["address"].lower().endswith(f"@{org_domain}")
    ]

    start_dt = ((event.get("start") or {}).get("dateTime") or "")[:19]
    end_dt = ((event.get("end") or {}).get("dateTime") or "")[:19]
    organizer = (event.get("organizer") or {}).get("emailAddress") or {}

    return {
        "id": f"google_cal_{event.get('id', '')}",
        "subject": event.get("subject") or "Google Meet",
        "start": {"dateTime": start_dt},
        "end": {"dateTime": end_dt},
        "organizer": {"emailAddress": {
            "name": organizer.get("name", ""),
            "address": organizer.get("address", ""),
        }},
        "attendees": attendees,
        "external_attendees": external_attendees,
        "has_transcript": False,
        "source": "google",
        "google_meet_code": event.get("_google_meet_code", ""),
        "event_id": event.get("id", ""),
    }


@main_bp.route("/google/dashboard")
@login_required
def google_dashboard():
    """Google Meet meetings dashboard — auto-loads from Outlook calendar for the selected date range."""
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    keyword = request.args.get("keyword", "").strip().lower()

    if not start_date:
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    token = get_token()
    if not token:
        flash("Your session has expired. Please sign in again.", "warning")
        return redirect(url_for("main.login"))

    meetings = []
    try:
        gc = GraphClient(token)
        raw_events = gc.search_google_meet_events(start_date, end_date)
        if keyword:
            raw_events = [
                e for e in raw_events
                if keyword in (e.get("subject") or "").lower()
            ]
        meetings = [_normalize_google_calendar_event(e) for e in raw_events]
    except Exception:
        logger.exception("google_dashboard calendar fetch failed")
        flash("Error fetching Google Meet events from calendar. Please try again.", "danger")

    return render_template(
        "google_dashboard.html",
        meetings=meetings,
        start_date=start_date,
        end_date=end_date,
        keyword=request.args.get("keyword", ""),
    )


@main_bp.route("/google/transcript")
@login_required
def google_transcript():
    """Fetch meeting details from Outlook calendar, try to auto-load transcript email, render transcript.html."""
    # GET — fetch meeting details from calendar, then try to auto-find transcript email
    event_id = request.args.get("event_id", "")
    meeting = None
    attendees = []
    transcript_text = ""
    auto_loaded = False

    if event_id:
        token = get_token()
        if token:
            try:
                gc = GraphClient(token)
                cal_event_id = event_id.removeprefix("google_cal_")
                raw_event = gc.get_event(cal_event_id)
                if raw_event:
                    meeting = _normalize_google_calendar_event(raw_event)
                    attendees = [
                        {"name": a["emailAddress"]["name"], "email": a["emailAddress"]["address"]}
                        for a in meeting.get("attendees", [])
                    ]

                    # Try to find a matching transcript email in the user's Outlook inbox
                    meeting_date = meeting["start"]["dateTime"][:10]
                    if meeting_date:
                        search_from = (
                            datetime.strptime(meeting_date, "%Y-%m-%d") - timedelta(days=1)
                        ).strftime("%Y-%m-%d")
                        search_to = (
                            datetime.strptime(meeting_date, "%Y-%m-%d") + timedelta(days=2)
                        ).strftime("%Y-%m-%d")

                        transcript_emails = gc.search_google_meet_emails(search_from, search_to)
                        subject_lower = (meeting["subject"] or "").lower()
                        subject_words = [w for w in subject_lower.split() if len(w) > 3]

                        for email_msg in transcript_emails:
                            email_subj = email_msg.get("subject", "").lower()
                            if subject_lower and (
                                subject_lower in email_subj
                                or any(w in email_subj for w in subject_words)
                            ):
                                full_msg = gc.get_email_message(email_msg["id"])
                                if full_msg:
                                    html_body = (full_msg.get("body") or {}).get("content", "")
                                    if html_body:
                                        parsed = _parse_google_meet_transcript(html_body)
                                        if parsed.strip():
                                            transcript_text = parsed
                                            auto_loaded = True
                                            break
            except Exception:
                logger.exception("google_transcript: failed to fetch calendar event or transcript email")

    if not meeting:
        flash("Could not load meeting details.", "warning")
        return redirect(url_for("main.google_dashboard"))

    user_data = session.get("user", {})
    user_email = (user_data.get("preferred_username") or user_data.get("upn") or "").lower()
    if user_email:
        record_meeting_access(user_email, meeting["subject"], meeting["start"]["dateTime"][:10])

    # Render transcript.html directly — user can type notes or upload a file even if no auto-transcript
    return render_template(
        "transcript.html",
        meeting=meeting,
        transcript_text=transcript_text,
        attendees_json=json.dumps(attendees),
        error=None,
        back_url=url_for("main.google_dashboard"),
        back_label="Google Meetings",
    )
