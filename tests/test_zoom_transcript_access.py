"""/zoom/transcript uses the account-wide Zoom token, so it must only show a meeting to its host,
its participants, or staff allowed to see the host's meetings."""
import pytest

from app import routes
from app.zoom_client import ZoomClient

HOST = "bob@cloudfuze.com"
VTT = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\nBob: SECRET-PLAN\n"


@pytest.fixture
def zoom(monkeypatch):
    calls = {"transcript": 0}
    monkeypatch.setattr(routes, "get_zoom_access_token", lambda: "zoom-token")
    monkeypatch.setattr(ZoomClient, "get_meeting_recordings", lambda self, mid: {
        "id": 81234567890, "uuid": "u1", "topic": "Acme call", "host_email": HOST,
        "start_time": "2026-10-20T10:00:00Z", "duration": 30,
        "recording_files": [{"file_type": "TRANSCRIPT", "status": "completed",
                             "download_url": "https://zoom.us/rec/t"}]})
    monkeypatch.setattr(ZoomClient, "get_meeting_participants", lambda self, mid: [
        {"user_email": HOST, "name": "Bob"}, {"user_email": "ana@cloudfuze.com", "name": "Ana"},
        {"user_email": "c@client.com", "name": "Client"}])

    def _transcript(self, url):
        calls["transcript"] += 1
        return VTT
    monkeypatch.setattr(ZoomClient, "get_transcript_content", _transcript)
    return calls


def test_non_participant_cannot_read_another_hosts_meeting(client, signed_in, zoom):
    signed_in(client, "eve@cloudfuze.com")
    resp = client.get("/zoom/transcript?meeting_id=81234567890")
    assert resp.status_code == 302
    assert "SECRET-PLAN" not in resp.get_data(as_text=True)
    assert zoom["transcript"] == 0


@pytest.mark.parametrize("viewer", [HOST, "ana@cloudfuze.com", "admin@cloudfuze.com"])
def test_host_participant_and_admin_can_read(client, signed_in, zoom, viewer):
    signed_in(client, viewer)
    resp = client.get("/zoom/transcript?meeting_id=81234567890")
    assert resp.status_code == 200 and "SECRET-PLAN" in resp.get_data(as_text=True)


def test_zoom_meeting_ids_are_encoded_in_api_paths(monkeypatch):
    seen = []

    class _Resp:
        status_code = 404
        text = ""

        def raise_for_status(self):
            raise RuntimeError("404")

    def _get(url, **kwargs):
        seen.append(url)
        return _Resp()

    monkeypatch.setattr("app.zoom_client._HTTP.get", _get)
    z = ZoomClient("t")
    z.get_meeting_recordings("../users/x/recordings?")
    with pytest.raises(RuntimeError):
        z.get_meeting_participants("../users/x")
    assert all("/users/x" not in u for u in seen), seen


def test_admin_can_open_recording_without_host_email(client, signed_in, zoom, monkeypatch):
    original = ZoomClient.get_meeting_recordings
    monkeypatch.setattr(ZoomClient, "get_meeting_recordings",
                        lambda self, mid: {**original(self, mid), "host_email": ""})
    signed_in(client, "admin@cloudfuze.com")
    assert client.get("/zoom/transcript?meeting_id=81234567890").status_code == 200


def test_truncated_participant_list_is_unknown(monkeypatch):
    class _Resp:
        status_code = 200

        def json(self):
            return {"participants": [{"user_email": "x@cloudfuze.com"}], "next_page_token": "more"}

    monkeypatch.setattr("app.zoom_client._HTTP.get", lambda url, **kw: _Resp())
    assert ZoomClient("t").get_past_meeting_participants("uuid-1") is None
