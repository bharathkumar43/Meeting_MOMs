"""The recording proxy re-checks, on every request, that the viewer may still see the owner's
meetings; a grant left in the session from an earlier role does not count."""
from app import meeting_sync

MANAGER = "mgr@cloudfuze.com"
EMPLOYEE = "emp@cloudfuze.com"


class _Upstream:
    status_code = 200
    headers = {"Content-Length": "3"}

    def iter_content(self, chunk_size):
        yield b"mp4"

    def close(self):
        pass


class _Zoom:
    def open_file_stream(self, url, range_header=None):
        return _Upstream()


def _grant(client, owner: str | None):
    entry = {"kind": "zoom", "download_url": "https://zoom.us/rec/v"}
    if owner is not None:
        entry["owner"] = owner
    with client.session_transaction() as sess:
        sess["recording_map"] = {"evt-1": entry}


def test_stream_refused_when_viewer_can_no_longer_see_owner(client, signed_in, monkeypatch):
    # The manager opened the employee's meeting earlier; the employee was since unassigned.
    monkeypatch.setattr(meeting_sync, "zoom_client_or_none", lambda: _Zoom())
    signed_in(client, MANAGER)
    _grant(client, EMPLOYEE)
    resp = client.get("/meetings/teams/recording/stream?event_id=evt-1")
    assert resp.status_code == 404


def test_stream_refused_for_grant_without_owner(client, signed_in, monkeypatch):
    monkeypatch.setattr(meeting_sync, "zoom_client_or_none", lambda: _Zoom())
    signed_in(client, MANAGER)
    _grant(client, None)
    assert client.get("/meetings/teams/recording/stream?event_id=evt-1").status_code == 404


def test_stream_served_for_own_recording(client, signed_in, monkeypatch):
    monkeypatch.setattr(meeting_sync, "zoom_client_or_none", lambda: _Zoom())
    signed_in(client, EMPLOYEE)
    _grant(client, EMPLOYEE)
    resp = client.get("/meetings/teams/recording/stream?event_id=evt-1")
    assert resp.status_code == 200 and resp.data == b"mp4"


def test_stream_served_to_admin_for_org_user(client, signed_in, monkeypatch):
    monkeypatch.setattr(meeting_sync, "zoom_client_or_none", lambda: _Zoom())
    signed_in(client, "admin@cloudfuze.com")
    _grant(client, EMPLOYEE)
    assert client.get("/meetings/teams/recording/stream?event_id=evt-1").status_code == 200
