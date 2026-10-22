"""Route behaviour: auth gates, admin/team pages, meetings list, MOM build/download/send."""
import io
from datetime import datetime, timedelta

import pytest
from docx import Document

from app import db
from app.models import CalendarSync, MeetingAccess, MeetingRecord, MOMSent, UserRole
from tests.conftest import make_user


def _seed_people(n_employees: int = 30):
    users = [make_user("admin@cloudfuze.com", "Admin")]
    users.append(make_user("boss@cloudfuze.com", "Boss Manager"))
    users += [make_user(f"emp{i}@cloudfuze.com", f"Employee {i}",
                        last_login=datetime(2026, 9, 1) + timedelta(hours=i))
              for i in range(n_employees)]
    db.session.add_all(users)
    db.session.add(UserRole(email="boss@cloudfuze.com", role="manager"))
    db.session.add_all(UserRole(email=f"emp{i}@cloudfuze.com", role="employee",
                                manager_email="boss@cloudfuze.com")
                       for i in range(0, n_employees, 2))
    db.session.add(CalendarSync(owner_email="emp0@cloudfuze.com",
                                last_synced_at=datetime(2026, 9, 30), last_error=""))
    db.session.flush()
    for u in users[2:12]:
        db.session.add(MeetingAccess(user_id=u.id, subject="Call", meeting_date="2026-09-01"))
        db.session.add(MOMSent(user_id=u.id, subject="Call", meeting_date="2026-09-01",
                               sent_to="c@client.com"))
    db.session.commit()


def test_protected_page_redirects_without_token(client, monkeypatch):
    from app import routes
    monkeypatch.setattr(routes, "get_token", lambda: None)
    resp = client.get("/dashboard")
    assert resp.status_code == 302
    assert "/login-page" in resp.headers["Location"]


def test_admin_page_lists_users_with_roles(app, client, signed_in, count_queries):
    with app.app_context():
        _seed_people()
    signed_in(client, "admin@cloudfuze.com", "Admin")
    with count_queries() as n:
        resp = client.get("/admin")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert html.count('class="overview-row"') == 32
    assert 'data-role-email="emp1@cloudfuze.com"' in html
    assert '<option value="boss@cloudfuze.com" selected>Boss Manager</option>' in html
    # Budget: constant number of queries regardless of user count (was 1 + 2N + 9)
    assert n["count"] <= 10, n["count"]


def test_admin_page_denied_for_employee(app, client, signed_in):
    with app.app_context():
        _seed_people(2)
    signed_in(client, "emp1@cloudfuze.com")
    resp = client.get("/admin")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/dashboard")


def test_team_page_shows_only_assigned_employees(app, client, signed_in, count_queries):
    with app.app_context():
        _seed_people(10)
    signed_in(client, "boss@cloudfuze.com", "Boss Manager")
    with count_queries() as n:
        resp = client.get("/team")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert html.count('class="overview-row"') == 5
    assert "emp0@cloudfuze.com" in html and "emp1@cloudfuze.com" not in html
    assert n["count"] <= 8, n["count"]


def test_dashboard_overview_counts(app, client, signed_in):
    with app.app_context():
        _seed_people(4)
        u = db.session.execute(db.select(db.text("id")).select_from(db.text("users"))
                               .where(db.text("email='emp0@cloudfuze.com'"))).scalar()
        db.session.add(MeetingAccess(user_id=u, subject="Pending call", meeting_date="2026-09-02"))
        db.session.commit()
    signed_in(client, "emp0@cloudfuze.com", "Employee 0")
    html = client.get("/dashboard").get_data(as_text=True)
    assert "Pending call" in html
    assert "Welcome back, Employee" in html


def test_teams_meetings_list_reads_from_database(app, client, signed_in):
    with app.app_context():
        _seed_people(1)
        db.session.add(MeetingRecord(
            owner_email="emp0@cloudfuze.com", event_id="evt-1", subject="Customer kickoff",
            start_utc=datetime.utcnow() - timedelta(days=1),
            end_utc=datetime.utcnow() - timedelta(days=1) + timedelta(hours=1),
            organizer_name="Org", organizer_email="emp0@cloudfuze.com",
            join_url="https://teams.microsoft.com/l/meetup-join/1",
            external_attendees_json='[{"name": "Client", "email": "c@client.com"}]',
            has_transcript=True, has_recording=False))
        db.session.commit()
    signed_in(client, "emp0@cloudfuze.com")
    resp = client.get("/meetings/teams")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Customer kickoff" in html
    assert "status-ok" in html


def _build_mom(client):
    return client.post("/send", data={
        "meeting_subject": "Kickoff", "meeting_date": "2026-10-01", "meeting_time": "10:00",
        "attendees_json": '[{"name": "Jane", "email": "jane.doe@client.com"}]',
        "transcript": "hello", "title": "Kickoff", "tldr": ["Agreed scope"],
        "action_desc": ["Send plan"], "action_assignee": ["Ana"], "action_due": [""],
        "decisions": ["Go"],
    })


def test_send_page_builds_document_and_download_returns_it(app, client, signed_in):
    with app.app_context():
        _seed_people(1)
    signed_in(client, "emp0@cloudfuze.com")
    page = _build_mom(client)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'value="jane.doe@client.com"' in html
    assert "Dear <span id=\"greetingPreview\" class=\"fw-semibold\">Jane</span>" in html

    dl = client.get("/download-mom")
    assert dl.status_code == 200
    assert "MOM_Kickoff_2026-10-01.docx" in dl.headers["Content-Disposition"]
    doc = Document(io.BytesIO(dl.data))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "MINUTES OF MEETING" in text and "Agreed scope" in text


def test_send_email_records_each_recipient(app, client, signed_in, monkeypatch):
    sent = {}

    def fake_send(**kwargs):
        sent.update(kwargs)
        return kwargs["attachments"][0]["filename"], kwargs["to_emails"]

    from app import routes
    monkeypatch.setattr(routes, "send_mom_email", fake_send)
    with app.app_context():
        _seed_people(1)
    signed_in(client, "emp0@cloudfuze.com")
    _build_mom(client)
    resp = client.post("/send-email", data={"to_email": "a@client.com, b@client.com",
                                            "format_choice": "docx"})
    assert resp.status_code == 200
    assert sent["attachments"][0]["filename"] == "MOM_Kickoff_2026-10-01.docx"
    assert sent["attachments"][0]["bytes"][:2] == b"PK"
    with app.app_context():
        assert {m.sent_to for m in MOMSent.query.filter_by(subject="Kickoff")} == {
            "a@client.com", "b@client.com"}


def test_send_email_without_recipient_shows_warning(app, client, signed_in):
    with app.app_context():
        _seed_people(1)
    signed_in(client, "emp0@cloudfuze.com")
    _build_mom(client)
    resp = client.post("/send-email", data={"to_email": "  ", "cc_emails": "x@client.com",
                                            "greeting_name": "Jane", "format_choice": "docx"})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Please provide at least one customer email address." in html
    assert 'value="x@client.com"' in html and "<strong>Action Items:</strong> 1" in html


def test_send_email_rejects_oversized_extra_attachment(app, client, signed_in, monkeypatch):
    from app import routes
    monkeypatch.setattr(routes, "_EXTRA_ATTACHMENT_MAX_BYTES", 10)
    monkeypatch.setattr(routes, "send_mom_email", lambda **k: pytest.fail("must not send"))
    with app.app_context():
        _seed_people(1)
    signed_in(client, "emp0@cloudfuze.com")
    _build_mom(client)
    resp = client.post("/send-email", data={
        "to_email": "a@client.com", "format_choice": "pdf",
        "extra_attachment": (io.BytesIO(b"x" * 11), "big.pdf")}, content_type="multipart/form-data")
    assert resp.status_code == 200
    assert "exceeds the 20 MB size limit" in resp.get_data(as_text=True)


def test_download_without_document_redirects(app, client, signed_in):
    signed_in(client, "emp0@cloudfuze.com")
    resp = client.get("/download-mom")
    assert resp.status_code == 302


def test_load_older_stops_when_backfill_cannot_fetch(app, client, signed_in, monkeypatch):
    from app import meeting_sync
    monkeypatch.setattr(meeting_sync, "BACKFILL_ENABLED", True)
    calls = []
    monkeypatch.setattr(meeting_sync, "ensure_backfill", lambda email, until: calls.append(until) or False)
    with app.app_context():
        _seed_people(1)
        db.session.add(CalendarSync(owner_email="emp1@cloudfuze.com", last_error="",
                                    last_synced_at=datetime.utcnow(),
                                    synced_from=datetime.utcnow() - timedelta(days=30)))
        db.session.commit()
    signed_in(client, "emp1@cloudfuze.com")
    resp = client.get("/meetings/teams/older?offset=0")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["count"] == 0
    assert len(calls) == 1


def test_send_page_tolerates_broken_attendees_field(app, client, signed_in):
    with app.app_context():
        _seed_people(1)
    signed_in(client, "emp0@cloudfuze.com")
    resp = client.post("/send", data={"meeting_subject": "S", "meeting_date": "2026-10-01",
                                      "attendees_json": "{not json", "title": "S"})
    assert resp.status_code == 200
    assert "Dear <span id=\"greetingPreview\" class=\"fw-semibold\">Customer</span>" in resp.get_data(as_text=True)


def test_built_mom_is_kept_out_of_the_session(app, client, signed_in, tmp_path):
    with app.app_context():
        _seed_people(1)
    signed_in(client, "emp0@cloudfuze.com")
    _build_mom(client)
    _build_mom(client)  # rebuilding replaces the previous file
    sessions = [p for p in (tmp_path / "sessions").iterdir() if p.is_file()]
    assert max(p.stat().st_size for p in sessions) < 10_000
    assert len(list((tmp_path / "uploads" / "generated").glob("*.docx"))) == 1
    assert client.get("/download-mom").data[:2] == b"PK"


def test_session_from_before_the_file_store_still_downloads(app, client, signed_in):
    from app.doc_generator import generate_mom_document
    raw = generate_mom_document("Old", "2026-10-01", "", [], [], [])
    signed_in(client, "emp0@cloudfuze.com")
    with client.session_transaction() as sess:
        sess["mom_doc"] = {"bytes_hex": raw.hex(), "meeting_subject": "Old",
                           "meeting_date": "2026-10-01", "meeting_time": "", "attendees": []}
    assert client.get("/download-mom").data == raw


def test_meeting_page_shows_uploaded_transcript(app, client, signed_in, monkeypatch, count_queries):
    from app import routes
    from app.models import MeetingUpload
    start = datetime(2026, 10, 1, 10, 0)
    event = {"id": "evt-9", "subject": "Customer review",
             "start": {"dateTime": "2026-10-01T10:00:00.0000000"},
             "end": {"dateTime": "2026-10-01T11:00:00.0000000"},
             "organizer": {"emailAddress": {"name": "Org", "address": "emp0@cloudfuze.com"}},
             "attendees": [{"emailAddress": {"name": "Client", "address": "c@client.com"}}],
             "onlineMeeting": {"joinUrl": ""}}
    monkeypatch.setattr(routes.GraphClient, "get_event", lambda self, event_id: dict(event))
    with app.app_context():
        _seed_people(1)
        rec = MeetingRecord(owner_email="emp0@cloudfuze.com", event_id="evt-9", subject="Customer review",
                            start_utc=start, end_utc=start + timedelta(hours=1),
                            join_url="https://teams.microsoft.com/l/x", has_transcript=False)
        db.session.add(rec)
        db.session.flush()
        db.session.add(MeetingUpload(meeting_record_id=rec.id, kind="transcript", original_name="t.vtt",
                                     transcript_text="WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Ana>Hello there</v>\n",
                                     transcript_format="vtt", uploaded_by="emp0@cloudfuze.com"))
        db.session.commit()
    signed_in(client, "emp0@cloudfuze.com")
    with count_queries() as n:
        resp = client.get("/transcript?event_id=evt-9")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Customer review" in html and "Hello there" in html and "Ana" in html
    assert n["count"] <= 12, n["count"]


def test_send_email_failure_does_not_show_internal_error(app, client, signed_in, monkeypatch):
    from app import routes

    def boom(**kwargs):
        raise RuntimeError("Graph 403 https://graph.microsoft.com/v1.0/me/sendMail INTERNAL-DETAIL")

    monkeypatch.setattr(routes, "send_mom_email", boom)
    with app.app_context():
        _seed_people(1)
    signed_in(client, "emp0@cloudfuze.com")
    _build_mom(client)
    html = client.post("/send-email", data={"to_email": "a@client.com", "format_choice": "docx"}
                       ).get_data(as_text=True)
    assert "Failed to send the email" in html and "INTERNAL-DETAIL" not in html


def test_parse_file_error_does_not_show_internal_error(client, signed_in):
    signed_in(client, "emp0@cloudfuze.com")
    resp = client.post("/transcript/parse-file", data={"file": (io.BytesIO(b"not a zip"), "x.docx")},
                       content_type="multipart/form-data")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 400 and "zip" not in body.lower() and "Could not read this file" in body


def test_parse_file_hides_unexpected_parser_errors(client, signed_in, monkeypatch):
    from app import routes

    def boom(raw):
        raise RuntimeError("INTERNAL-DETAIL at C:/srv/app/parser.py")

    monkeypatch.setattr(routes, "_text_from_uploaded_vtt", boom)
    signed_in(client, "emp0@cloudfuze.com")
    resp = client.post("/transcript/parse-file", data={"file": (io.BytesIO(b"WEBVTT"), "t.vtt")},
                       content_type="multipart/form-data")
    assert resp.status_code == 400 and "INTERNAL-DETAIL" not in resp.get_data(as_text=True)
