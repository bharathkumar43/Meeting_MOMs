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
    request, session, flash, send_file, jsonify, Response, stream_with_context, current_app,
)
import io
from docx import Document
from werkzeug.utils import secure_filename

from app.auth import get_auth_url, process_auth_callback, get_token, get_app_token, logout as auth_logout
from app.graph_client import GraphClient
from app import db, meeting_insights, meeting_sync, meeting_uploads, roles, timefmt
from app.meeting_sync import (
    candidate_user_ids as _app_user_ids_for_transcript_lookup,
    lookup_meeting_media as _lookup_meeting_media,
    record_media, record_to_meeting,
)
from app.models import CalendarSync, MeetingAnalysis, MeetingRecord, MeetingUpload, User
from app.zoom_auth import get_zoom_access_token
from app.zoom_client import ZoomClient
from app.meeting_filter import (

    parse_vtt_transcript, transcript_to_readable, transcript_view_lines,
)
from app.doc_generator import (
    convert_docx_to_pdf, generate_mom_document, load_generated_doc, store_generated_doc,
)
from app.email_sender import send_mom_email
from app.mom_generator import SKILLS, analyze_meeting_conversation, generate_mom_from_transcript
from app.activity_tracker import (
    record_login, record_meeting_access, record_mom_sent,
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
    today = timefmt.local_today()
    return {
        "tz_name": timefmt.TZ_NAME,
        "nav_user": {
            "name": name or email,
            "email": email,
            "initials": initials,
            "is_admin": bool(email) and roles.is_admin(email),
            "is_manager": bool(email) and roles.is_manager(email),
        },
        "date_presets": {
            "today": today.strftime("%Y-%m-%d"),
            "presets": [
                ("Today", today.strftime("%Y-%m-%d")),
                ("7 days", (today - timedelta(days=7)).strftime("%Y-%m-%d")),
                ("30 days", (today - timedelta(days=30)).strftime("%Y-%m-%d")),
            ],
        },
    }


@main_bp.app_template_filter("nice_date")
def nice_date(value) -> str:
    """UTC timestamp -> local 'Mon, 19 Oct 2026'; a date-only 'YYYY-MM-DD' is shown as is."""
    day = timefmt.local_date(value)
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%a, %d %b %Y")
    except ValueError:
        return value or ""


@main_bp.app_template_filter("hm")
def hm(value) -> str:
    """UTC timestamp -> local 'HH:MM'."""
    return timefmt.local_hm(value)


@main_bp.app_template_filter("local_date")
def local_date(value) -> str:
    """UTC timestamp -> local 'YYYY-MM-DD' (used for meeting dates/keys)."""
    return timefmt.local_date(value)


@main_bp.app_template_filter("local_dt")
def local_dt(value, fmt: str = "%d %b %Y, %H:%M") -> str:
    """Naive-UTC datetime -> local formatted string."""
    local = timefmt.to_local(value)
    return local.strftime(fmt) if local else ""


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
        if not roles.is_admin(_current_user_email()):
            flash("Access denied. Admin privileges required.", "danger")
            return redirect(url_for("main.dashboard"))
        return f(*args, **kwargs)
    return decorated


def staff_required(f):
    """Admins and managers (per-user access is then checked with roles.can_view)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_token()
        if not token:
            return redirect(url_for("main.login_page"))
        email = _current_user_email()
        if not (roles.is_admin(email) or roles.is_manager(email)):
            flash("Access denied.", "danger")
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
            # Signing in enrols the user in meeting sync (latest window fetched in background)
            meeting_sync.start_initial_sync(current_app._get_current_object(), email)
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


def _local_range_utc(start_date: str, end_date: str) -> tuple[str, str, datetime, datetime]:
    """
    Filter dates are local (IST) calendar days. Returns the normalized 'YYYY-MM-DD' strings
    and the naive-UTC [start, end] bounds covering those whole local days.
    Raises ValueError on a malformed date.
    """
    today = timefmt.local_today()
    start = datetime.strptime(start_date or (today - timedelta(days=7)).strftime("%Y-%m-%d"), "%Y-%m-%d")
    end = datetime.strptime(end_date or today.strftime("%Y-%m-%d"), "%Y-%m-%d")
    return (start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
            timefmt.local_day_bounds_utc(start)[0], timefmt.local_day_bounds_utc(end)[1])


def _teams_query(owner_email: str, keyword: str, start: datetime | None = None,
                 end: datetime | None = None, source: str | None = "teams"):
    """Stored customer meetings for one user, newest first, optional range + subject terms.
    source: 'teams' (default) / 'zoom', or None for every source.
    Only meetings that have already started are listed (no transcript/MOM exists before)."""
    now = datetime.utcnow()
    q = meeting_sync.source_filter(MeetingRecord.query, source).filter(
        MeetingRecord.owner_email == owner_email,
        MeetingRecord.start_utc <= min(end or now, now),
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


ATTENDANCE_FILTERS = {"attended": "Attended", "absent": "Didn't attend"}


def _attendance_filter(query, attendance: str):
    """Restrict a MeetingRecord query to meetings the owner attended / didn't attend."""
    if attendance not in ATTENDANCE_FILTERS:
        return query
    from app.models import MeetingAttendance
    return query.join(MeetingAttendance).filter(
        MeetingAttendance.attended.is_(attendance == "attended")
    )


def _history_complete(state) -> bool:
    """True when no older history will be fetched (always, when backfill is disabled)."""
    if not meeting_sync.BACKFILL_ENABLED:
        return True
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
    return f"{local_dt(value, '%d %b %H:%M')} {timefmt.TZ_NAME}"


@main_bp.route("/meetings/teams")
@login_required
def teams_meetings():
    """Teams customer meetings, read from the database (kept fresh by meeting_sync)."""
    return _meetings_page("teams")


def _meetings_page(source: str):
    """Teams or Zoom meetings list for the signed-in user (from the database)."""
    endpoint = "main.teams_meetings" if source == "teams" else "main.zoom_dashboard"
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    keyword = request.args.get("keyword", "")
    attendance = request.args.get("attendance", "")
    if attendance not in ATTENDANCE_FILTERS:
        attendance = ""
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
            list_query = _attendance_filter(_teams_query(email, keyword, source=source), attendance)
            rows = list_query.limit(_TEAMS_PAGE_SIZE + 1).all()
            if len(rows) > _TEAMS_PAGE_SIZE or not _history_complete(state):
                next_offset = min(len(rows), _TEAMS_PAGE_SIZE)
            rows = rows[:_TEAMS_PAGE_SIZE]
            list_total = list_query.count()  # stored so far (older history may still load)
        else:
            start_date, end_date, start, end = _local_range_utc(start_date, end_date)
            if state and state.synced_from and start < state.synced_from:
                meeting_sync.ensure_backfill(email, start)  # range older than stored history
            rows = _attendance_filter(
                _teams_query(email, keyword, start, end, source=source), attendance
            ).all()
            list_total = len(rows)
    except ValueError:
        flash("Invalid date range.", "warning")
        return redirect(url_for(endpoint))

    return render_template(
        "teams_meetings.html",
        meetings=[record_to_meeting(r) for r in rows],
        start_date="" if all_mode else start_date,
        end_date="" if all_mode else end_date,
        keyword=keyword,
        all_mode=all_mode,
        next_offset=next_offset,
        list_total=list_total,
        last_synced_at=state.last_synced_at if state else None,
        page_source=source,
        page_endpoint=endpoint,
        attendance=attendance,
        attendance_filters=ATTENDANCE_FILTERS,
    )


@main_bp.route("/meetings/teams/older")
@login_required
def teams_meetings_older():
    """Next page of the "All" view (from the database; backfills history when it runs out)."""
    keyword = request.args.get("keyword", "")
    source = request.args.get("source", "teams")
    if source not in ("teams", "zoom"):
        source = "teams"
    attendance = request.args.get("attendance", "")
    try:
        offset = max(int(request.args.get("offset", "0")), 0)
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid offset."}), 400
    email = _current_user_email()

    try:
        rows = _attendance_filter(_teams_query(email, keyword, source=source), attendance).offset(offset).limit(_TEAMS_PAGE_SIZE + 1).all()
        state = meeting_sync.get_sync_state(email)
        # Ran out of stored meetings but history isn't complete: fetch older months now
        while len(rows) <= _TEAMS_PAGE_SIZE and not _history_complete(state):
            reached = state.synced_from if state and state.synced_from else datetime.utcnow()
            oldest = datetime.utcnow() - timedelta(days=meeting_sync.BACKFILL_DAYS)
            until = max(reached - timedelta(days=meeting_sync.BACKFILL_CHUNK_DAYS), oldest)
            if not meeting_sync.ensure_backfill(email, until):
                break  # nothing fetched (no app token / user not found): don't spin forever
            state = meeting_sync.get_sync_state(email)
            rows = _attendance_filter(_teams_query(email, keyword, source=source), attendance).offset(offset).limit(_TEAMS_PAGE_SIZE + 1).all()
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

    hosted = (meeting_sync.get_record(_current_user_email(), event_id)
              if event_id.startswith(meeting_sync.ZOOM_HOSTED_PREFIX) else None)
    if hosted is not None:
        # Zoom meeting the user hosted with no Outlook invite: the stored record is the event
        meeting = meeting_sync.record_to_meeting(hosted)
    else:
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
    is_zoom_meeting = stored is not None and meeting_sync.source_of_join_url(stored.join_url) == "zoom"
    if is_zoom_meeting:
        # Zoom invite: video + transcript come from the CloudFuze Zoom account, not Teams
        try:
            cached_media, transcript_text, transcript_lines = _zoom_media_and_transcript(
                stored, meeting_sync.record_to_meeting(stored)
            )
        except Exception:
            db.session.rollback()
            logger.exception("Zoom media lookup failed for %s", meeting.get("subject", ""))
        tried_app_lookup = True
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
                transcripts = meeting_sync.for_occurrence(
                    app_client0.list_transcripts(mid0, user_id=uid0), meeting
                )
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
    if is_zoom_meeting:
        skip_lookups = True  # Teams lookups don't apply to Zoom meetings
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
            transcripts = meeting_sync.for_occurrence(client.list_transcripts(meeting_id), meeting)
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
                    transcripts = meeting_sync.for_occurrence(
                        app_client.list_transcripts(meeting_id, user_id=uid), meeting
                    )
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
            timefmt.local_date(meeting.get("start", {}).get("dateTime", "")),
        )

    recording = None
    app_token_rec = get_app_token()
    if app_token_rec:
        recording = _recording_for_meeting(meeting, GraphClient(app_token_rec))

    record = meeting_sync.get_record(_current_user_email(), meeting["id"])
    transcript_text, transcript_lines, recording, uploads = _with_uploads(
        record, transcript_text, transcript_lines, recording
    )

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
        can_upload=record is not None,
        upload_owner=_current_user_email(),
        uploads=uploads,
        can_analyze=record is not None and bool(transcript_text or transcript_lines),
        analysis=_analysis_of(record),
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
        # Already looked up and stored — reuse, including "no recording" (but never Zoom
        # media saved before the host/participant check)
        entry = None if meeting_sync.zoom_media_unchecked(stored, media) else media.get("recording")
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
            session["recording_map"] = dict(list(recording_map.items())[-_RECORDING_MAP_LIMIT:])

    return _playable_recording(entry, meeting["id"], app_client, _current_user_email())


def _playable_recording(entry: dict | None, event_id: str, app_client,
                        owner_email: str) -> dict | None:
    """
    Turn a stored recording entry into player URLs. Recordings-API entries stream through
    teams_recording_stream, which only serves entries placed in this session's map, and
    only while the viewer may still see the meeting owner's meetings.
    """
    if not entry:
        return None
    if entry.get("kind") in ("api", "zoom"):
        recording_map = dict(session.get("recording_map", {}))
        recording_map[event_id] = {**entry, "owner": (owner_email or "").lower()}
        session["recording_map"] = dict(list(recording_map.items())[-_RECORDING_MAP_LIMIT:])
        stream_url = url_for("main.teams_recording_stream", event_id=event_id)
        return {"src": stream_url, "download_url": f"{stream_url}&download=1", "web_url": ""}
    if entry.get("kind") == "sharepoint":  # Teams channel meeting: the team's SharePoint
        file_info = app_client.get_drive_item_file(entry["drive_id"], entry["item_id"])
    else:
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
    if not entry or entry.get("kind") not in ("api", "zoom"):
        return Response("Recording not available.", status=404)
    # Re-check on every request: a role or team change since the page was opened applies now
    if not roles.can_view(_current_user_email(), entry.get("owner") or ""):
        return Response("Recording not available.", status=404)

    if entry["kind"] == "zoom":
        # Zoom download URLs need the account token, so they never reach the browser
        zoom = meeting_sync.zoom_client_or_none()
        if zoom is None:
            return Response("Recording temporarily unavailable.", status=503)
        upstream = zoom.open_file_stream(entry["download_url"], request.headers.get("Range"))
    else:
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
    if entry["kind"] == "zoom":
        headers["Content-Type"] = "video/mp4"  # Zoom serves MP4s as octet-stream
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


# ── Manual uploads (video / transcript) ──────────────────────

def _upload_record_for_request():
    """
    (MeetingRecord, error_response) for an upload request. The record must belong to the
    signed-in user, or — for admins only — to the org user named in the 'owner' field.
    """
    owner = (request.form.get("owner") or "").strip().lower()
    event_id = request.form.get("event_id", "")
    me = _current_user_email()
    if owner and owner != me:
        if not _admin_target_email(owner):
            return None, (jsonify({"status": "error", "message": "Not allowed."}), 403)
    else:
        owner = me
    record = meeting_sync.get_record(owner, event_id) if event_id else None
    if record is None:
        return None, (jsonify({"status": "error", "message": "Meeting not found."}), 404)
    return record, None


@main_bp.route("/meetings/upload/video", methods=["POST"])
@login_required
def upload_meeting_video():
    """Drag-and-drop / browse upload of a meeting video (owner or admin)."""
    if request.method != "POST":
        return jsonify({"status": "error", "message": "POST required."}), 405
    record, error = _upload_record_for_request()
    if error:
        return error
    file = request.files.get("video")
    if not file or not file.filename:
        return jsonify({"status": "error", "message": "Choose a video file."}), 400
    try:
        meeting_uploads.save_video(record, file, _current_user_email())
    except meeting_uploads.UploadError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception:
        db.session.rollback()
        logger.exception("Video upload failed for record %s", record.id)
        return jsonify({"status": "error", "message": "Upload failed. Please try again."}), 500
    return jsonify({"status": "ok", "data": {"saved": True}}), 200


@main_bp.route("/meetings/upload/transcript", methods=["POST"])
@login_required
def upload_meeting_transcript():
    """Upload and keep a transcript (.vtt keeps timestamps; .txt / .docx as text)."""
    if request.method != "POST":
        return jsonify({"status": "error", "message": "POST required."}), 405
    record, error = _upload_record_for_request()
    if error:
        return error
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"status": "error", "message": "Choose a transcript file."}), 400
    ext = os.path.splitext(secure_filename(file.filename).lower())[1]
    fmt = meeting_uploads.TRANSCRIPT_FORMATS.get(ext)
    if not fmt:
        return jsonify({"status": "error", "message": "Please upload a .vtt, .txt or .docx file."}), 400
    raw = file.read(TRANSCRIPT_UPLOAD_MAX_BYTES + 1)
    if len(raw) > TRANSCRIPT_UPLOAD_MAX_BYTES:
        return jsonify({"status": "error", "message": "File is too large (maximum 10 MB)."}), 413
    try:
        text = _text_from_uploaded_docx(raw) if fmt == "docx" else _decode_text_file(raw)
        meeting_uploads.save_transcript(record, file.filename, fmt, text, _current_user_email())
    except meeting_uploads.UploadError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception:
        db.session.rollback()
        logger.exception("Transcript upload failed for record %s", record.id)
        return jsonify({"status": "error", "message": "Could not read this file."}), 400
    return jsonify({"status": "ok", "data": {"saved": True}}), 200


@main_bp.route("/meetings/upload/<int:upload_id>/video")
@login_required
def uploaded_video(upload_id):
    """Serve an uploaded video (supports seeking) to its meeting's owner or an admin."""
    upload = db.session.get(MeetingUpload, upload_id)
    record = db.session.get(MeetingRecord, upload.meeting_record_id) if upload else None
    me = _current_user_email()
    if (upload is None or upload.kind != "video" or record is None
            or not roles.can_view(me, record.owner_email)):
        return Response("Not found.", status=404)
    path = meeting_uploads.video_path(upload)
    if not os.path.exists(path):
        return Response("Not found.", status=404)
    response = send_file(
        path, mimetype=upload.content_type, conditional=True,
        as_attachment=request.args.get("download") == "1",
        download_name=upload.original_name or f"meeting-{record.id}.mp4",
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _teams_transcript(app_client, media: dict | None, meeting: dict) -> tuple[str, list]:
    """(text, lines) of a stored Teams meeting's transcript for this occurrence, or ("", [])."""
    media = media or {}
    if not media.get("has_transcript") or not media.get("online_meeting_id"):
        return "", []
    try:
        uid, mid = media["user_id"], media["online_meeting_id"]
        transcripts = meeting_sync.for_occurrence(app_client.list_transcripts(mid, user_id=uid), meeting)
        if transcripts:
            vtt = app_client.get_transcript_content(mid, transcripts[0]["id"], user_id=uid)
            if vtt:
                entries = parse_vtt_transcript(vtt)
                return transcript_to_readable(entries), transcript_view_lines(entries)
    except Exception:
        logger.exception("Transcript fetch failed for %s", meeting.get("subject", ""))
    return "", []


def _record_transcript(rec) -> tuple[str, list]:
    """(text, lines) for a stored meeting: Teams or Zoom transcript, else an uploaded one."""
    meeting = record_to_meeting(rec)
    text, lines = "", []
    if meeting["source"] == "zoom":
        _, text, lines = _zoom_media_and_transcript(rec, meeting)
    elif rec.has_transcript:
        app_token = get_app_token()
        if app_token:
            text, lines = _teams_transcript(GraphClient(app_token), record_media(rec), meeting)
    text, lines, _, _ = _with_uploads(rec, text, lines, recording=True)
    return text, lines


def _analysis_of(rec) -> dict | None:
    """Saved analysis of this meeting (whoever ran it), as rendered on the page; None if never run."""
    row = meeting_insights.shared_analysis(rec)
    if row is None:
        return None
    try:
        result = json.loads(row.result_json)
    except ValueError:
        return None
    result["meta"] = {"at": timefmt.to_local(row.created_at).strftime("%d %b %Y, %H:%M ") + timefmt.TZ_NAME}
    return result


@main_bp.route("/meetings/analyze", methods=["POST"])
@login_required
def analyze_meeting():
    """
    On-demand AI review of how CloudFuze employees handled a client meeting (JSON
    {owner, event_id, refresh?}). Saved per meeting, so it runs again only on refresh.
    """
    if request.method != "POST" or not request.is_json:
        return jsonify({"status": "error", "message": "Expected JSON."}), 400
    payload = request.get_json(silent=True) or {}
    owner = str(payload.get("owner") or "").strip().lower()
    event_id = str(payload.get("event_id") or "")
    me = _current_user_email()
    if owner and owner != me and not _admin_target_email(owner):
        return jsonify({"status": "error", "message": "Not allowed."}), 403
    rec = meeting_sync.get_record(owner or me, event_id) if event_id else None
    if rec is None:
        return jsonify({"status": "error", "message": "Meeting not found."}), 404

    if not payload.get("refresh"):
        saved = _analysis_of(rec)
        if saved:
            return jsonify({"status": "ok", "data": saved}), 200

    text, lines = _record_transcript(rec)
    if not text and not lines:
        return jsonify({"status": "error",
                        "message": "This meeting has no transcript to analyze."}), 422

    # Who is CloudFuze: the owner, a CloudFuze organizer, and any speaker matching a staff account
    owner_user = User.query.filter_by(email=rec.owner_email).first()
    internal = [owner_user.name if owner_user and owner_user.name else rec.owner_email]
    if rec.organizer_email.lower().endswith(f"@{Config.ORG_DOMAIN.lower()}"):
        internal.append(rec.organizer_name)
    staff = meeting_insights.org_users()
    internal += [sp for sp in {line.get("speaker", "") for line in lines}
                 if sp and meeting_insights.email_for(sp, staff)]
    external = [meeting_insights.attendee_name(a)
                for a in record_to_meeting(rec).get("external_attendees", []) if isinstance(a, dict)]

    result, usage, error = analyze_meeting_conversation(lines, text, rec.subject, internal, external)
    if error:
        return jsonify({"status": "error", "message": error}), 502
    result = meeting_insights.attach_metrics(result, lines, external)
    # One analysis per meeting: re-analyzing replaces the shared one
    row = meeting_insights.shared_analysis(rec) or MeetingAnalysis(meeting_record_id=rec.id)
    row.result_json = json.dumps(result)
    row.model = usage["model"][:64]
    row.input_tokens, row.output_tokens = usage["input_tokens"], usage["output_tokens"]
    row.created_by, row.created_at = me, datetime.utcnow()
    db.session.add(row)
    db.session.commit()
    logger.info("Analyzed %r for %s by %s (%s, %d in / %d out tokens)", rec.subject,
                rec.owner_email, me, row.model, row.input_tokens, row.output_tokens)
    return jsonify({"status": "ok", "data": _analysis_of(rec)}), 200


def _with_uploads(record, transcript_text: str, transcript_lines: list, recording):
    """
    Fall back to uploaded files when Teams has nothing: returns
    (transcript_text, transcript_lines, recording, uploads_info) for the meeting page.
    """
    info = {"video": None, "transcript": None}
    if record is None:
        return transcript_text, transcript_lines, recording, info
    if not recording:
        video = meeting_uploads.latest(record, "video")
        if video and os.path.exists(meeting_uploads.video_path(video)):
            src = url_for("main.uploaded_video", upload_id=video.id)
            recording = {"src": src, "download_url": f"{src}?download=1", "web_url": ""}
            info["video"] = video
    if not transcript_text:
        tx = meeting_uploads.latest(record, "transcript")
        if tx and tx.transcript_text:
            info["transcript"] = tx
            if tx.transcript_format == "vtt":
                entries = parse_vtt_transcript(tx.transcript_text)
                transcript_text = transcript_to_readable(entries) or tx.transcript_text
                transcript_lines = transcript_view_lines(entries)
            else:
                transcript_text = tx.transcript_text
    return transcript_text, transcript_lines, recording, info


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

    try:
        attendees = [a for a in json.loads(attendees_json) if isinstance(a, dict)]
    except (ValueError, TypeError):
        attendees = []  # tampered/broken hidden field: just no prefilled recipients

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

    previous = (session.get("mom_doc") or {}).get("doc_id")
    session["mom_doc"] = {
        "doc_id": store_generated_doc(doc_bytes, replaces=previous),
        "meeting_subject": title,
        "meeting_date": meeting_date,
        "meeting_time": meeting_time,
        "attendees": attendees,
        "action_count": len(action_items),
        "decision_count": len(decisions),
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


def _mom_doc_bytes(mom_data: dict | None) -> bytes | None:
    """Bytes of the MOM built on the Send page (sessions from before the file store hold hex)."""
    if not mom_data:
        return None
    if mom_data.get("bytes_hex"):
        return bytes.fromhex(mom_data["bytes_hex"])
    return load_generated_doc(mom_data.get("doc_id"))


# ── Download MOM ─────────────────────────────────────────────

@main_bp.route("/download-mom")
@login_required
def download_mom():
    mom_data = session.get("mom_doc")
    doc_bytes = _mom_doc_bytes(mom_data)
    if doc_bytes is None:
        flash("No document available to download. Please generate a MOM first.", "warning")
        return redirect(url_for("main.dashboard"))

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

_EXTRA_ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024  # 20 MB


def _send_form_again(mom_data: dict, to_email: str, cc_emails: str, greeting_name: str) -> str:
    """Re-show the Send page with what the user typed (after a validation problem)."""
    return render_template(
        "send.html",
        meeting_subject=mom_data["meeting_subject"],
        meeting_date=mom_data["meeting_date"],
        meeting_time=mom_data["meeting_time"],
        action_count=mom_data.get("action_count", 0),
        decision_count=mom_data.get("decision_count", 0),
        sent_success=False,
        prefill_email=to_email,
        prefill_cc=cc_emails,
        has_default_cc=bool(Config.MOM_DEFAULT_DL_EMAIL or Config.MOM_DEFAULT_CC_EMAIL),
        greeting_name=greeting_name,
    )


@main_bp.route("/send-email", methods=["POST"])
@login_required
def send_email():
    mom_data = session.get("mom_doc")
    doc_bytes = _mom_doc_bytes(mom_data)
    if doc_bytes is None:
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
        return _send_form_again(mom_data, to_email, cc_emails, greeting_name)

    # Check the optional extra file before any conversion work; never read more than the limit
    extra_file = request.files.get("extra_attachment")
    extra_bytes = None
    if extra_file and extra_file.filename:
        extra_bytes = extra_file.read(_EXTRA_ATTACHMENT_MAX_BYTES + 1)
        if len(extra_bytes) > _EXTRA_ATTACHMENT_MAX_BYTES:
            flash("Extra attachment exceeds the 20 MB size limit.", "warning")
            return _send_form_again(mom_data, to_email, cc_emails, greeting_name)

    try:
        token = get_token()

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
        if extra_bytes is not None:
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
    user_dashboard_stats = get_all_user_dashboard_stats(include_counts=False)

    return render_template(
        "admin.html",
        user_dashboard_stats=user_dashboard_stats,
        # Meeting-sync enrolment per user: {email: CalendarSync} (missing = not synced yet)
        sync_states={row.owner_email: row for row in CalendarSync.query.all()},
        **_role_context(user_dashboard_stats),
    )


def _role_context(user_rows) -> dict:
    """Role, manager and dropdown data for the admin users table."""
    emails = [u["email"].lower() for u in user_rows]
    roles.prefetch(emails)
    names = {u["email"].lower(): (u["name"] or u["email"]) for u in user_rows}
    user_roles = {e: roles.role_of(e) for e in emails}
    return {
        "user_roles": user_roles,
        "user_managers": {e: roles.manager_of(e) for e in emails},
        "locked_admins": {e for e in emails if roles.is_env_admin(e)},
        "manager_choices": sorted(
            [(e, names[e]) for e in emails if user_roles[e] == "manager"], key=lambda m: m[1].lower()
        ),
        "role_labels": roles.ROLE_LABELS,
        "org_suffix": f"@{Config.ORG_DOMAIN}".lower(),
    }


@main_bp.route("/admin/users/<path:user_email>/role", methods=["POST"])
@admin_required
def admin_set_role(user_email):
    """Admin only: set a user's role and/or manager (JSON {role?, manager?})."""
    if request.method != "POST" or not request.is_json:
        return jsonify({"status": "error", "message": "Expected JSON."}), 400
    payload = request.get_json(silent=True) or {}
    role = payload.get("role") if "role" in payload else None
    manager = payload.get("manager") if "manager" in payload else None
    if role is not None and not isinstance(role, str) or manager is not None and not isinstance(manager, str):
        return jsonify({"status": "error", "message": "Invalid value."}), 400
    try:
        row = roles.assign(user_email, role, manager, by=_current_user_email())
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception:
        db.session.rollback()
        logger.exception("admin_set_role failed for %s", user_email)
        return jsonify({"status": "error", "message": "Could not save. Please try again."}), 500
    return jsonify({"status": "ok", "data": {
        "role": roles.role_of(row.email), "manager": row.manager_email,
    }}), 200


@main_bp.route("/team")
@staff_required
def team():
    """Manager's page: their assigned employees (admins see the full admin panel instead)."""
    me = _current_user_email()
    if roles.is_admin(me) and not roles.is_manager(me):
        return redirect(url_for("main.admin_dashboard"))
    team_emails = set(roles.team_of(me))
    rows = [u for u in get_all_user_dashboard_stats(include_counts=False)
            if u["email"].lower() in team_emails]
    return render_template(
        "team.html",
        team_rows=rows,
        sync_states={r.owner_email: r for r in CalendarSync.query.filter(
            CalendarSync.owner_email.in_(team_emails)).all()} if team_emails else {},
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

_ADMIN_PAGE_SIZE = 50


def _admin_target_email(user_email: str) -> str | None:
    """
    Normalized org email the signed-in admin/manager may open, or None.
    Admins: any account in our tenant. Managers: only employees assigned to them.
    """
    email = (user_email or "").strip().lower()
    if not email.endswith(f"@{Config.ORG_DOMAIN}".lower()) or "/" in email:
        return None
    if not roles.can_view(_current_user_email(), email):
        return None
    return email


def _people_home() -> str:
    """Where admins/managers go back to: the admin panel or the manager's team page."""
    if roles.is_admin(_current_user_email()):
        return url_for("main.admin_dashboard")
    return url_for("main.team")


@main_bp.route("/admin/user-meetings/<path:user_email>")
@staff_required
def admin_user_meetings(user_email):
    """An employee's Teams customer meetings (from the database), with transcript/video."""
    email = _admin_target_email(user_email)
    if not email:
        flash("You don't have access to this user's meetings.", "warning")
        return redirect(_people_home())

    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    keyword = request.args.get("keyword", "")
    source = request.args.get("source", "")
    if source not in meeting_sync.SOURCES:
        source = ""
    attendance = request.args.get("attendance", "")
    if attendance not in ATTENDANCE_FILTERS:
        attendance = ""
    error = None

    # Only users who signed in since sync was enabled are synced automatically; an admin
    # can still pull someone in deliberately with the Sync button
    state = meeting_sync.get_sync_state(email)
    not_enrolled = state is None
    if state is not None and state.last_error and state.last_synced_at is None:
        error = (
            f"Couldn't load Microsoft meetings for {email}. The account may not exist in "
            "this tenant, or the app lacks permission to read its calendar."
        )

    all_mode = not start_date and not end_date
    next_offset = None
    try:
        if all_mode:
            list_query = _attendance_filter(
                _teams_query(email, keyword, source=source or None), attendance)
            rows = list_query.limit(_ADMIN_PAGE_SIZE + 1).all()
            if len(rows) > _ADMIN_PAGE_SIZE:
                next_offset = _ADMIN_PAGE_SIZE
            rows = rows[:_ADMIN_PAGE_SIZE]
            list_total = list_query.count()
        else:
            start_date, end_date, start, end = _local_range_utc(start_date, end_date)
            if state and state.synced_from and start < state.synced_from:
                meeting_sync.ensure_backfill(email, start)
            rows = _attendance_filter(
                _teams_query(email, keyword, start, end, source=source or None), attendance
            ).all()
            list_total = len(rows)
    except ValueError:
        flash("Invalid date range.", "warning")
        return redirect(url_for("main.admin_user_meetings", user_email=email))

    from app.models import User as UserModel
    db_user = UserModel.query.filter_by(email=email).first()
    sent_keys = set()
    if db_user:
        sent_keys = {f"{s.subject}|{s.meeting_date}" for s in db_user.sent_moms.all()}

    # Summary cards: every source combined for the chosen date range (ignores the source
    # dropdown, search and the list limit)
    if all_mode:
        kpi_query = _teams_query(email, "", source=None)
    else:
        _, _, kpi_start, kpi_end = _local_range_utc(start_date, end_date)
        kpi_query = _teams_query(email, "", kpi_start, kpi_end, source=None)
    kpi = {"total": 0, "sent": 0, "by_source": {key: 0 for key in meeting_sync.SOURCES}}
    for subject, start_utc, join_url in kpi_query.with_entities(
        MeetingRecord.subject, MeetingRecord.start_utc, MeetingRecord.join_url
    ):
        kpi["total"] += 1
        kpi["by_source"][meeting_sync.source_of_join_url(join_url)] += 1
        if f"{subject}|{timefmt.local_date(start_utc)}" in sent_keys:
            kpi["sent"] += 1
    kpi["pending"] = kpi["total"] - kpi["sent"]

    return render_template(
        "admin_user_meetings.html",
        user_email=email,
        user_display_name=(db_user.name if db_user and db_user.name else email),
        meetings=[record_to_meeting(r) for r in rows],
        start_date="" if all_mode else start_date,
        end_date="" if all_mode else end_date,
        keyword=keyword,
        all_mode=all_mode,
        next_offset=next_offset,
        list_total=list_total,
        last_synced_at=state.last_synced_at if state else None,
        error=error,
        not_enrolled=not_enrolled,
        people_home=_people_home(),
        source=source,
        sources=meeting_sync.SOURCES,
        attendance=attendance,
        attendance_filters=ATTENDANCE_FILTERS,
        kpi=kpi,
        progress=meeting_insights.employee_progress(email, db_user.name if db_user else ""),
        skills=SKILLS,
    )


@main_bp.route("/admin/user-meetings/<path:user_email>/older")
@staff_required
def admin_user_meetings_older(user_email):
    """Next page of an employee's meetings for the admin list (from the database)."""
    email = _admin_target_email(user_email)
    try:
        offset = max(int(request.args.get("offset", "0")), 0)
    except ValueError:
        offset = -1
    if not email or offset < 0:
        return jsonify({"status": "error", "message": "Bad request."}), 400
    source = request.args.get("source", "")
    keyword = request.args.get("keyword", "")
    rows = _attendance_filter(_teams_query(
        email, keyword, source=source if source in meeting_sync.SOURCES else None
    ), request.args.get("attendance", "")).offset(offset).limit(_ADMIN_PAGE_SIZE + 1).all()
    has_more = len(rows) > _ADMIN_PAGE_SIZE
    rows = rows[:_ADMIN_PAGE_SIZE]

    return jsonify({"status": "ok", "data": {
        "html": render_template(
            "_admin_rows.html", meetings=[record_to_meeting(r) for r in rows],
            user_email=email, sources=meeting_sync.SOURCES,
        ),
        "count": len(rows),
        "next_offset": offset + len(rows) if has_more else None,
    }}), 200


@main_bp.route("/admin/user-meetings/<path:user_email>/status", methods=["POST"])
@staff_required
def admin_user_meetings_status(user_email):
    """Transcript/video status for one employee's stored meetings (admin only)."""
    email = _admin_target_email(user_email)
    if not email or request.method != "POST" or not request.is_json:
        return jsonify({"status": "error", "message": "Bad request."}), 400
    payload = request.get_json(silent=True, force=True) or {}
    ids = [i for i in payload.get("ids", []) if isinstance(i, str)][:25]
    records = MeetingRecord.query.filter(
        MeetingRecord.owner_email == email, MeetingRecord.event_id.in_(ids)
    ).all() if ids else []
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
            logger.exception("admin_user_meetings_status failed for %s", email)
            return jsonify({"status": "error", "message": "Could not check meetings."}), 502
    return jsonify({"status": "ok", "data": {
        r.event_id: {"transcript": bool(r.has_transcript), "video": bool(r.has_recording)}
        for r in records if r.has_transcript is not None
    }}), 200


@main_bp.route("/admin/user-meetings/<path:user_email>/sync", methods=["POST"])
@staff_required
def admin_user_meetings_sync(user_email):
    """Admin Sync button for one employee (same 60 s cooldown as the user's own button)."""
    email = _admin_target_email(user_email)
    if not email or request.method != "POST":
        return jsonify({"status": "error", "message": "Bad request."}), 400
    logger.info("Admin %s synced meetings of %s", _current_user_email(), email)
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


@main_bp.route("/admin/sync-all", methods=["POST"])
@admin_required
def admin_sync_all():
    """Admin panel: sync every org user in the background (returns immediately)."""
    if request.method != "POST":
        return jsonify({"status": "error", "message": "POST required."}), 405
    started, count = meeting_sync.start_sync_all(current_app._get_current_object())
    logger.info("Admin %s requested sync of all users (started=%s)", _current_user_email(), started)
    if not started:
        return jsonify({"status": "ok", "data": {
            "started": False, "message": "A sync of all users is already running.",
        }}), 200
    return jsonify({"status": "ok", "data": {"started": True, "users": count}}), 202


@main_bp.route("/admin/user-meetings/<path:user_email>/meeting")
@staff_required
def admin_user_meeting(user_email):
    """Read-only video + transcript of one employee's meeting (admin only, audited in logs)."""
    email = _admin_target_email(user_email)
    event_id = request.args.get("event_id", "")
    back = url_for("main.admin_user_meetings", user_email=email) if email else _people_home()
    rec = meeting_sync.get_record(email, event_id) if email and event_id else None
    if rec is None:
        flash("Meeting not found for this user.", "warning")
        return redirect(back)

    logger.info("Admin %s opened meeting %r of %s", _current_user_email(), rec.subject, email)
    meeting = record_to_meeting(rec)
    app_token = get_app_token()
    if not app_token:
        flash("Microsoft access is temporarily unavailable.", "danger")
        return redirect(back)
    app_client = GraphClient(app_token)

    is_teams = meeting["source"] == "teams"
    is_zoom = meeting["source"] == "zoom"
    media = record_media(rec) if rec.has_transcript is not None else None
    transcript_text, transcript_lines = "", []
    if is_zoom:
        media, transcript_text, transcript_lines = _zoom_media_and_transcript(rec, meeting)
    elif media is None or not media.get("online_meeting_id"):
        try:
            media = _lookup_meeting_media(
                app_client, meeting, _app_user_ids_for_transcript_lookup(app_client, meeting, signed_in_email=email)
            )
            meeting_sync.save_media(rec, media)
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Admin media lookup failed for %s", email)
            media = media or {}

    if is_teams:
        transcript_text, transcript_lines = _teams_transcript(app_client, media, meeting)

    recording = _playable_recording(media.get("recording"), rec.event_id, app_client,
                                    rec.owner_email)
    transcript_text, transcript_lines, recording, uploads = _with_uploads(
        rec, transcript_text, transcript_lines, recording
    )

    return render_template(
        "transcript.html",
        meeting=meeting,
        transcript_text=transcript_text,
        transcript_lines=transcript_lines,
        recording=recording,
        attendees_json="[]",
        error=None,
        back_url=back,
        back_label=email,
        read_only=True,
        can_upload=True,
        upload_owner=email,
        uploads=uploads,
        source_note=_source_note(meeting, media),
        can_analyze=bool(transcript_text or transcript_lines),
        analysis=_analysis_of(rec),
    )


def _source_note(meeting: dict, media: dict | None) -> str | None:
    """Explain missing media for Zoom meetings (None when nothing to say)."""
    source = meeting.get("source")
    if source == "zoom" and not (media or {}).get("recording") and not (media or {}).get("has_transcript"):
        return ("No Zoom cloud recording was found for this meeting. It's only available when a "
                "CloudFuze Zoom host recorded it to the cloud; client-hosted Zoom calls can't be "
                "loaded here — use Upload instead.")
    return None


def _zoom_media_and_transcript(rec, meeting: dict) -> tuple[dict, str, list]:
    """Zoom invite -> (media, transcript_text, transcript_lines) from the CloudFuze Zoom account."""
    media = record_media(rec) if rec.has_transcript is not None else None
    zoom = meeting_sync.zoom_client_or_none()
    # Media stored before the ownership check (no bound_to) is looked up again once
    unbound = meeting_sync.zoom_media_unchecked(rec, media)
    if media is None or not media.get("recording_api_ok") or unbound:
        media = meeting_sync.lookup_zoom_media(zoom, meeting, rec.owner_email)
        meeting_sync.save_media(rec, media)
        db.session.commit()
    text, lines = "", []
    if media.get("transcript_url") and zoom is not None:
        vtt = zoom.get_transcript_content(media["transcript_url"])
        if vtt:
            entries = parse_vtt_transcript(vtt)
            text, lines = transcript_to_readable(entries), transcript_view_lines(entries)
    return media, text, lines


@main_bp.route("/zoom/dashboard")
@login_required
def zoom_dashboard():
    """Zoom meetings from the user's calendar, matched to CloudFuze Zoom cloud recordings."""
    return _meetings_page("zoom")


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
            timefmt.local_date(meeting.get("start", {}).get("dateTime", "")),
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


# ── Transcript file helpers ──────────────────────────────────────────────────


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


