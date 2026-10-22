"""Resource limits: request size per route, .docx expansion, AI input size, video storage quota,
per-user rate limits, the health endpoint and the production secret-key guard."""
import io
import zipfile
from datetime import datetime

import pytest

from app import db, meeting_uploads, mom_generator, rate_limit, routes
from app.models import MeetingRecord, MeetingUpload
from config import Config

ME = "emp0@cloudfuze.com"


def _record(owner=ME, event_id="evt-1"):
    rec = MeetingRecord(owner_email=owner, event_id=event_id, subject="Customer call",
                        start_utc=datetime(2026, 10, 20, 10), end_utc=datetime(2026, 10, 20, 11),
                        join_url="https://teams.microsoft.com/l/meetup-join/1",
                        first_seen_at=datetime(2026, 10, 1), updated_at=datetime(2026, 10, 1))
    db.session.add(rec)
    db.session.commit()
    return rec


def _video(size: int) -> io.BytesIO:
    return io.BytesIO(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * (size - 12))


# ── Request size ────────────────────────────────────────────────────────────

def test_non_video_routes_reject_video_sized_bodies(client, signed_in):
    signed_in(client, ME)
    body = b"transcript=" + b"a" * (Config.REQUEST_MAX_BYTES + 1024)
    resp = client.post("/mom-builder", data=body, content_type="application/x-www-form-urlencoded")
    assert resp.status_code == 413


def test_video_upload_still_accepts_more_than_the_general_limit(app, client, signed_in, monkeypatch):
    monkeypatch.setattr(Config, "REQUEST_MAX_BYTES", 64 * 1024)
    with app.app_context():
        _record()
    signed_in(client, ME)
    resp = client.post("/meetings/upload/video", data={"event_id": "evt-1", "video": (_video(200 * 1024), "m.mp4")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_data(as_text=True)


# ── .docx expansion ─────────────────────────────────────────────────────────

def _docx_bomb(expanded: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", b"<" * expanded)
    return buf.getvalue()


def test_docx_bomb_is_refused_before_parsing():
    with pytest.raises(ValueError):
        routes._text_from_uploaded_docx(_docx_bomb(Config.DOCX_MAX_EXPANDED_BYTES + 1024))


def test_parse_file_refuses_docx_bomb(client, signed_in):
    signed_in(client, ME)
    raw = _docx_bomb(Config.DOCX_MAX_EXPANDED_BYTES + 1024)
    assert len(raw) < routes.TRANSCRIPT_UPLOAD_MAX_BYTES
    resp = client.post("/transcript/parse-file", data={"file": (io.BytesIO(raw), "x.docx")},
                       content_type="multipart/form-data")
    assert resp.status_code == 400


# ── AI input size ───────────────────────────────────────────────────────────

def test_oversized_transcript_is_not_sent_to_openai(monkeypatch):
    monkeypatch.setattr(Config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(mom_generator, "_client", lambda: pytest.fail("OpenAI must not be called"))
    _, error = mom_generator.generate_mom_from_transcript("x" * (Config.MOM_TRANSCRIPT_MAX_CHARS + 1), "S")
    assert error and "too long" in error


# ── Video storage quota ─────────────────────────────────────────────────────

class _File:
    def __init__(self, data: io.BytesIO, name="m.mp4"):
        self.stream, self.filename = data, name


def test_video_quota_per_uploader(app, monkeypatch):
    monkeypatch.setattr(Config, "VIDEO_USER_QUOTA_BYTES", 300 * 1024)
    with app.app_context():
        first, second = _record(event_id="e1"), _record(event_id="e2")
        meeting_uploads.save_video(first, _File(_video(200 * 1024)), ME)
        with pytest.raises(meeting_uploads.UploadError):
            meeting_uploads.save_video(second, _File(_video(200 * 1024)), ME)
        assert MeetingUpload.query.filter_by(kind="video").count() == 1
        # Replacing the same meeting's video does not double-count the old file
        meeting_uploads.save_video(first, _File(_video(250 * 1024)), ME)


def test_video_refused_when_disk_is_nearly_full(app, monkeypatch):
    monkeypatch.setattr(meeting_uploads, "_free_bytes", lambda path: Config.UPLOAD_MIN_FREE_BYTES - 1)
    with app.app_context():
        with pytest.raises(meeting_uploads.UploadError):
            meeting_uploads.save_video(_record(), _File(_video(100 * 1024)), ME)


# ── Rate limits ─────────────────────────────────────────────────────────────

def test_per_user_rate_limit_on_transcript_parsing(client, signed_in, monkeypatch):
    monkeypatch.setitem(Config.RATE_LIMITS, "parse_file", (2, 3600))
    signed_in(client, ME)
    statuses = [client.post("/transcript/parse-file", data={"file": (io.BytesIO(b"hello"), "t.txt")},
                            content_type="multipart/form-data").status_code for _ in range(3)]
    assert statuses == [200, 200, 429]


def test_rate_limit_is_per_user(app, monkeypatch):
    monkeypatch.setitem(Config.RATE_LIMITS, "parse_file", (1, 3600))
    with app.app_context():
        assert rate_limit.hit("parse_file", "a@cloudfuze.com")
        assert not rate_limit.hit("parse_file", "a@cloudfuze.com")
        assert rate_limit.hit("parse_file", "b@cloudfuze.com")


# ── Health endpoint ─────────────────────────────────────────────────────────

def test_healthz_is_public_and_minimal(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200 and resp.get_json() == {"status": "ok"}


# ── Secret key guard ────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["dev-secret-key-change-in-production", "your-flask-secret-key-here", "short"])
def test_https_deployment_refuses_weak_secret_key(monkeypatch, tmp_path, key):
    monkeypatch.setattr(Config, "REDIRECT_URI", "https://moms.example.com/auth/callback")
    monkeypatch.setattr(Config, "SECRET_KEY", key)
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{tmp_path / 'k.db'}")
    from app import create_app
    with pytest.raises(RuntimeError):
        create_app()


def test_onedrive_transcript_download_is_capped(monkeypatch):
    from app.graph_client import GraphClient

    class _Resp:
        status_code = 200
        closed = False

        def iter_content(self, chunk_size):
            for _ in range(100):
                yield b"x" * chunk_size

        def close(self):
            _Resp.closed = True

    monkeypatch.setattr("app.graph_client._HTTP.get", lambda url, **kw: _Resp())
    assert GraphClient("t").get_onedrive_file_content("u", "i", max_bytes=1024 * 1024) is None
    assert _Resp.closed
