import json
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, session, flash, send_file,
)
import io

from app.auth import get_auth_url, process_auth_callback, get_token, logout as auth_logout
from app.graph_client import GraphClient
from app.meeting_filter import (
    filter_customer_meetings, filter_by_subject,
    parse_vtt_transcript, transcript_to_readable,
)
from app.doc_generator import generate_mom_document
from app.email_sender import send_mom_email
from app.mom_generator import generate_mom_from_transcript
from app.activity_tracker import record_login, record_meeting_access, get_all_users, get_user_stats
from config import Config

main_bp = Blueprint("main", __name__)


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


# ── Dashboard ────────────────────────────────────────────────

@main_bp.route("/dashboard")
@login_required
def dashboard():
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    keyword = request.args.get("keyword", "")

    meetings = None

    if not start_date:
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    if start_date and end_date:
        try:
            token = get_token()
            client = GraphClient(token)
            events = client.list_calendar_events(start_date, end_date)

            meetings = filter_customer_meetings(events, Config.ORG_DOMAIN)

            if keyword:
                keywords = [k.strip() for k in keyword.split(",")]
                meetings = filter_by_subject(meetings, keywords)

        except Exception as e:
            flash(f"Error fetching meetings: {str(e)}", "danger")
            meetings = []

    return render_template(
        "dashboard.html",
        meetings=meetings,
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

    # Try to fetch transcript via the online meeting
    try:
        online_meeting = client.get_online_meeting_by_join_url(join_url)
        if online_meeting:
            meeting_id = online_meeting["id"]
            transcripts = client.list_transcripts(meeting_id)
            if transcripts:
                transcript_id = transcripts[0]["id"]
                vtt_content = client.get_transcript_content(meeting_id, transcript_id)
                entries = parse_vtt_transcript(vtt_content)
                transcript_text = transcript_to_readable(entries)
            else:
                error = "No transcripts found for this meeting. Was transcription enabled during the call?"
        else:
            error = "Could not resolve the online meeting. The meeting may not have transcription enabled."
    except Exception as e:
        error = f"Could not fetch transcript: {str(e)}"

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

    # Store in session for download and email sending
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

    to_email = request.form.get("to_email", "")
    if not to_email:
        flash("Please provide a customer email address.", "warning")
        return redirect(url_for("main.send_page"))

    try:
        token = get_token()
        doc_bytes = bytes.fromhex(mom_data["bytes_hex"])
        filename = send_mom_email(
            access_token=token,
            to_email=to_email,
            meeting_title=mom_data["meeting_subject"],
            meeting_date=mom_data["meeting_date"],
            doc_bytes=doc_bytes,
        )
        flash(f"MOM sent successfully to {to_email}!", "success")

        return render_template(
            "send.html",
            meeting_subject=mom_data["meeting_subject"],
            meeting_date=mom_data["meeting_date"],
            meeting_time=mom_data["meeting_time"],
            discussion_count=0,
            action_count=0,
            decision_count=0,
            sent_success=True,
            sent_to=to_email,
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
    total_users, total_meetings = get_user_stats()
    return render_template(
        "admin.html",
        users=users,
        total_users=total_users,
        total_meetings=total_meetings,
    )
