"""/transcript: caller-supplied parameters must not steer navigation or app-token lookups."""
import pytest

from app import routes
from app.graph_client import GraphClient

OWNER = "emp@cloudfuze.com"
VICTIM_OID = "11111111-1111-1111-1111-111111111111"


def _event(online_meeting=None, organizer=OWNER, subject="Acme weekly sync"):
    return {
        "id": "evt-1", "subject": subject,
        "start": {"dateTime": "2026-10-20T10:00:00.0000000"},
        "end": {"dateTime": "2026-10-20T11:00:00.0000000"},
        "organizer": {"emailAddress": {"address": organizer}},
        "attendees": [], "onlineMeeting": online_meeting,
    }


@pytest.fixture
def event(monkeypatch):
    holder = {"event": _event()}
    monkeypatch.setattr(GraphClient, "get_event", lambda self, eid: holder["event"])
    return holder


@pytest.mark.parametrize("back", ["javascript:alert(1)", "//evil.example/x", "https://evil.example/",
                                  "/\\evil.example", " javascript:alert(1)"])
def test_back_link_rejects_non_local_targets(client, signed_in, event, back):
    signed_in(client, OWNER)
    html = client.get("/transcript", query_string={"event_id": "evt-1", "back": back}).get_data(as_text=True)
    assert "evil.example" not in html and "javascript:" not in html


def test_back_link_keeps_local_paths(client, signed_in, event):
    signed_in(client, OWNER)
    html = client.get("/transcript", query_string={"event_id": "evt-1", "back": "/meetings/teams?page=2"})
    assert 'href="/meetings/teams?page=2"' in html.get_data(as_text=True)


def test_event_with_null_online_meeting_renders(client, signed_in, event):
    signed_in(client, OWNER)
    event["event"] = _event(online_meeting=None)
    assert client.get("/transcript?event_id=evt-1").status_code == 200


def test_get_event_encodes_the_id(monkeypatch):
    seen = {}

    class _Resp:
        status_code = 404

    def _get(url, **kwargs):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr("app.graph_client._HTTP.get", _get)
    GraphClient("t").get_event("../../me?x=1#")
    assert seen["url"].endswith("/me/events/%2E%2E%2F%2E%2E%2Fme%3Fx%3D1%23")


class _Lookup:
    """App-token client stand-in: resolves emails to object IDs."""
    ids = {OWNER: "uid-owner", "boss@cloudfuze.com": VICTIM_OID, "x@client.com": None}

    def _resolve_user_id_safe(self, email):
        return self.ids.get(email.lower())


def _teams_link(oid):
    return ("https://teams.microsoft.com/l/meetup-join/19%3ameeting_x%40thread.v2/0?context="
            "%7b%22Tid%22%3a%22t%22%2c%22Oid%22%3a%22" + oid + "%22%7d")


def test_onedrive_search_includes_organizer_only_when_join_link_agrees():
    meeting = _event(online_meeting={"joinUrl": _teams_link(VICTIM_OID)}, organizer="boss@cloudfuze.com")
    assert routes._onedrive_transcript_targets(_Lookup(), meeting, OWNER) == ["uid-owner", VICTIM_OID]


def test_onedrive_search_ignores_spoofable_organizer_without_matching_link():
    # Organizer address from an (external) invite does not match the Microsoft-issued link
    meeting = _event(online_meeting={"joinUrl": _teams_link("22222222-2222-2222-2222-222222222222")},
                     organizer="boss@cloudfuze.com")
    assert routes._onedrive_transcript_targets(_Lookup(), meeting, OWNER) == ["uid-owner"]
    no_link = _event(online_meeting=None, organizer="boss@cloudfuze.com")
    assert routes._onedrive_transcript_targets(_Lookup(), no_link, OWNER) == ["uid-owner"]


@pytest.mark.parametrize("file_subject,meeting_subject,expected", [
    ("Acme weekly sync", "Acme weekly sync", True),
    ("Acme weekly sync", "Acme  weekly sync | Q4", True),
    ("Board review confidential", "a", False),
    ("Board review confidential", "", False),
    ("Sync", "Sync with Acme team", False),
])
def test_subject_match_requires_a_meaningful_overlap(file_subject, meeting_subject, expected):
    assert routes._subject_match(file_subject, meeting_subject) is expected


def test_back_link_keeps_the_lists_own_absolute_url(client, signed_in, event):
    # _teams_rows.html passes request.url (absolute, same host) from the Teams and Zoom lists
    signed_in(client, OWNER)
    back = "http://localhost/meetings/zoom?keyword=acme&start_date=2026-10-01"
    html = client.get("/transcript", query_string={"event_id": "evt-1", "back": back}).get_data(as_text=True)
    assert 'href="/meetings/zoom?keyword=acme&amp;start_date=2026-10-01"' in html


@pytest.mark.parametrize("event_id", ["", ".", ".."])
def test_empty_or_dot_event_ids_are_not_found(client, signed_in, monkeypatch, event_id):
    monkeypatch.setattr(GraphClient, "get_event", lambda self, eid: {"value": []})
    signed_in(client, OWNER)
    resp = client.get("/transcript", query_string={"event_id": event_id})
    assert resp.status_code == 302


def test_zoom_dot_segment_ids_stay_one_segment(monkeypatch):
    from app.zoom_client import _path_id
    assert _path_id("..") == "%2E%2E" and _path_id(".") == "%2E"
