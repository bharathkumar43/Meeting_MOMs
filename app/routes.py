import json
import logging
import re
from datetime import datetime, timedelta
from functools import wraps

logger = logging.getLogger(__name__)

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, session, flash, send_file,
)
import io

from app.auth import get_auth_url, process_auth_callback, get_token, get_app_token, logout as auth_logout
from app.graph_client import GraphClient
from app.meeting_filter import (
    filter_customer_meetings, filter_by_subject,
    parse_vtt_transcript, transcript_to_readable,
)
from app.doc_generator import generate_mom_document
from app.email_sender import send_mom_email
from app.mom_generator import generate_mom_from_transcript
from app.activity_tracker import (
    record_login, record_meeting_access, record_mom_sent,
    get_all_users, get_user_stats, get_pending_moms, get_sent_moms,
    get_managers, get_non_managers,
)
from config import Config

main_bp = Blueprint("main", __name__)


def _normalize_subject(s):
    """Normalize a meeting subject for comparison.
    Teams strips special chars like | from recording filenames, so we must
    remove them and collapse whitespace for both sides to match."""
    s = s.lower().strip()
    s = re.sub(r'[|/\\:*?"<>]', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s


def _match_recording(meeting, all_recordings):
    """Check if a calendar event has a matching recording file in OneDrive."""
    if not all_recordings:
        return False
    meeting_start = meeting.get("start", {}).get("dateTime", "")
    if not meeting_start:
        return False
    meeting_date = meeting_start[:10].replace("-", "")
    norm_subject = _normalize_subject(meeting.get("subject", ""))
    for rec in all_recordings:
        if rec["date"] != meeting_date:
            continue
        norm_rec = _normalize_subject(rec["subject"])
        if norm_rec == norm_subject or norm_rec in norm_subject or norm_subject in norm_rec:
            return True
    return False


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
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    keyword = request.args.get("keyword", "")

    recorded_meetings = None
    unrecorded_meetings = None

    if not start_date:
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    if start_date and end_date:
        try:
            token = get_token()
            client = GraphClient(token)
            logger.info("Dashboard: fetching events from %s to %s", start_date, end_date)
            events = client.list_calendar_events(start_date, end_date)
            logger.info("Dashboard: got %d online meeting events", len(events))

            meetings = filter_customer_meetings(events, Config.ORG_DOMAIN)
            logger.info("Dashboard: %d customer meetings after filter", len(meetings))

            if keyword:
                keywords = [k.strip() for k in keyword.split(",")]
                meetings = filter_by_subject(meetings, keywords)

            app_token = get_app_token()
            app_client = GraphClient(app_token) if app_token else None

            all_recordings = []
            if app_client:
                user_profile = client.get_user_profile()
                dashboard_user_id = user_profile.get("id", "")
                if dashboard_user_id:
                    all_recordings = app_client.build_recording_lookup(
                        meetings, dashboard_user_id, Config.ORG_DOMAIN
                    )

            recorded_meetings = []
            unrecorded_meetings = []
            for meeting in meetings:
                has_recording = _match_recording(meeting, all_recordings)
                meeting["has_transcript"] = has_recording
                if has_recording:
                    recorded_meetings.append(meeting)
                else:
                    unrecorded_meetings.append(meeting)

        except Exception as e:
            flash(f"Error fetching meetings: {str(e)}", "danger")
            recorded_meetings = []
            unrecorded_meetings = []

    return render_template(
        "dashboard.html",
        recorded_meetings=recorded_meetings,
        unrecorded_meetings=unrecorded_meetings,
        start_date=start_date,
        end_date=end_date,
        keyword=keyword,
    )


# ── Transcript View ──────────────────────────────────────────

@main_bp.route("/transcript")
@login_required
def transcript():
    event_id = request.args.get("event_id", "")
    join_url = request.args.get("join_url", "")

    token = get_token()
    client = GraphClient(token)

    # Fetch the calendar event details
    try:
        events = client.list_calendar_events(
            (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d"),
            datetime.now().strftime("%Y-%m-%d"),
        )
        meeting = next((e for e in events if e["id"] == event_id), None)
    except Exception:
        meeting = None

    if not meeting:
        flash("Meeting not found.", "warning")
        return redirect(url_for("main.dashboard"))

    # Build attendees list
    attendees = []
    for att in meeting.get("attendees", []):
        attendees.append({
            "name": att.get("emailAddress", {}).get("name", ""),
            "email": att.get("emailAddress", {}).get("address", ""),
        })

    transcript_text = ""
    error = None
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

    # Step 1: Try delegated token (works when user has OnlineMeetings.Read consent)
    try:
        online_meeting = client.get_online_meeting_by_join_url(effective_join_url)
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
                    logger.info("Step 1 SUCCESS: loaded %d chars", len(transcript_text))
        else:
            logger.info("Step 1: No online meeting found via delegated token")
    except Exception as e:
        logger.warning("Step 1 FAILED: %s", e)

    # Step 2: If delegated didn't work, try app token
    if not transcript_text:
        app_token = get_app_token()
        if app_token and effective_join_url:
            app_client = GraphClient(app_token)

            user_data = session.get("user", {})
            signed_in_email = (
                user_data.get("preferred_username") or user_data.get("upn") or ""
            )
            targets = []
            if organizer_email:
                targets.append(("organizer", organizer_email))
            if signed_in_email and signed_in_email.lower() != (organizer_email or "").lower():
                targets.append(("signed-in user", signed_in_email))

            for label, target_email in targets:
                if transcript_text:
                    break
                try:
                    uid = app_client._resolve_user_id_safe(target_email)
                    if not uid:
                        logger.info("Step 2: Cannot resolve %s (%s)", label, target_email)
                        continue
                    logger.info("Step 2: Trying via %s (%s)", label, target_email)
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
                            logger.info("Step 2 SUCCESS via %s: %d chars", label, len(transcript_text))
                except Exception as e:
                    logger.warning("Step 2 FAILED via %s: %s", label, e)

    if not transcript_text and not error:
        consent_url = (
            f"https://login.microsoftonline.com/{Config.AZURE_TENANT_ID}"
            f"/adminconsent?client_id={Config.AZURE_CLIENT_ID}"
        )
        error = (
            "No transcript available. Possible reasons: "
            "(1) Transcription was not enabled during the call (recording alone is not enough). "
            "(2) The Teams Application Access Policy is not configured for this app. "
            "Ask your Teams Admin to run the PowerShell commands, or grant admin consent at: "
            f"{consent_url} . "
            "You can still create a MOM by typing your notes below."
        )

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
        attendees_json=json.dumps(attendees),
        error=error,
    )


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
        ai_summary=mom["summary"],
        ai_discussion_points=mom["discussion_points"],
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
    duration = request.form.get("duration", "")
    summary = request.form.get("summary", "")
    discussion_points = request.form.getlist("discussion_points")
    discussion_points = [p for p in discussion_points if p.strip()]

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
        duration=duration,
        attendees=attendees,
        summary=summary,
        discussion_points=discussion_points,
        action_items=action_items,
        decisions=decisions,
        transcript_text=transcript if include_transcript else "",
    )

    # Extract external attendee emails for pre-filling
    org_domain = Config.ORG_DOMAIN.lower()
    external_emails = [
        a["email"] for a in attendees
        if a.get("email") and not a["email"].lower().endswith(f"@{org_domain}")
    ]
    primary_email = external_emails[0] if external_emails else ""
    additional_emails = ", ".join(external_emails[1:]) if len(external_emails) > 1 else ""

    session["mom_doc"] = {
        "bytes_hex": doc_bytes.hex(),
        "meeting_subject": title,
        "meeting_date": meeting_date,
        "meeting_time": meeting_time,
    }

    return render_template(
        "send.html",
        meeting_subject=title,
        meeting_date=meeting_date,
        meeting_time=meeting_time,
        discussion_count=len(discussion_points),
        action_count=len(action_items),
        decision_count=len(decisions),
        sent_success=False,
        prefill_email=primary_email,
        prefill_cc=additional_emails,
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

    all_emails = [e.strip() for e in (to_email + "," + cc_emails).split(",") if e.strip()]
    if not all_emails:
        flash("Please provide at least one customer email address.", "warning")
        return redirect(url_for("main.send_page"))

    try:
        token = get_token()
        doc_bytes = bytes.fromhex(mom_data["bytes_hex"])
        filename, sent_list = send_mom_email(
            access_token=token,
            to_emails=all_emails,
            meeting_title=mom_data["meeting_subject"],
            meeting_date=mom_data["meeting_date"],
            doc_bytes=doc_bytes,
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
            discussion_count=0,
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
            discussion_count=0,
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

    manager_emails = Config.MANAGER_EMAILS
    manager_pending = [p for p in pending_moms if p.user_email in manager_emails]
    manager_sent = [s for s in sent_moms if s.user_email in manager_emails]

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
    )


# ── Admin: Per-User Meetings View ────────────────────────────

@main_bp.route("/admin/user-meetings/<path:user_email>")
@admin_required
def admin_user_meetings(user_email):
    start_date = request.args.get(
        "start_date",
        (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
    )
    end_date = request.args.get(
        "end_date",
        datetime.now().strftime("%Y-%m-%d"),
    )

    recorded_meetings = []
    unrecorded_meetings = []
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
                has_recording = _match_recording(meeting, all_recordings)
                meeting["has_transcript"] = has_recording
                if has_recording:
                    recorded_meetings.append(meeting)
                else:
                    unrecorded_meetings.append(meeting)

    except Exception as e:
        error = f"Error fetching meetings for {user_email}: {str(e)}"

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
        recorded_meetings=recorded_meetings,
        unrecorded_meetings=unrecorded_meetings,
        sent_keys=sent_keys,
        start_date=start_date,
        end_date=end_date,
        error=error,
    )
