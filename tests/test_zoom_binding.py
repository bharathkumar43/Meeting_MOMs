"""A Zoom link in someone's calendar must only attach a cloud recording that person hosted or
attended, never another host's recording that happens to share the meeting ID and slot."""
import json
from datetime import datetime

import pytest

from app import db, meeting_sync
from app.models import MeetingRecord

OWNER = "alice@cloudfuze.com"
MID = "81234567890"


def _meeting() -> dict:
    return {
        "start": {"dateTime": "2026-10-20T10:00:00.0000000"},
        "end": {"dateTime": "2026-10-20T11:00:00.0000000"},
        "organizer": {"emailAddress": {"address": OWNER}},
        "onlineMeeting": {"joinUrl": f"https://zoom.us/j/{MID}"},
    }


def _recording(host: str) -> dict:
    return {
        "id": int(MID), "uuid": "uuid-1", "host_email": host,
        "start_time": "2026-10-20T10:02:00Z",
        "recording_files": [
            {"file_type": "MP4", "status": "completed", "download_url": "https://zoom.us/rec/v"},
            {"file_type": "TRANSCRIPT", "status": "completed", "download_url": "https://zoom.us/rec/t"},
        ],
    }


class FakeZoom:
    def __init__(self, host: str, participants: list[str] | None):
        self.host, self.participants = host, participants

    def list_account_recordings(self, start, end):
        return [_recording(self.host)]

    def get_transcript_content(self, url):
        return "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\nBob: confidential\n"

    def get_past_meeting_participants(self, uuid):
        if self.participants is None:
            return None
        return [{"user_email": e, "name": e} for e in self.participants]


@pytest.fixture(autouse=True)
def _fresh_index():
    meeting_sync._ZOOM_INDEX._data.clear()  # account recordings are cached per month window
    meeting_sync._ZOOM_PARTICIPANTS._data.clear()
    yield


def test_other_hosts_recording_is_not_attached(db_session):
    media = meeting_sync.lookup_zoom_media(FakeZoom("bob@cloudfuze.com", ["bob@cloudfuze.com"]),
                                           _meeting(), OWNER)
    assert media["recording"] is None
    assert not media["has_transcript"] and media["transcript_url"] is None


def test_own_hosted_recording_is_attached(db_session):
    media = meeting_sync.lookup_zoom_media(FakeZoom(OWNER, []), _meeting(), OWNER)
    assert media["recording"]["download_url"] == "https://zoom.us/rec/v"
    assert media["bound_to"] == OWNER


def test_recording_the_owner_attended_is_attached(db_session):
    zoom = FakeZoom("bob@cloudfuze.com", ["bob@cloudfuze.com", "Alice@CloudFuze.com"])
    media = meeting_sync.lookup_zoom_media(zoom, _meeting(), OWNER)
    assert media["has_transcript"] and media["transcript_url"] == "https://zoom.us/rec/t"


def test_unknown_participants_retry_later_instead_of_attaching(db_session):
    media = meeting_sync.lookup_zoom_media(FakeZoom("bob@cloudfuze.com", None), _meeting(), OWNER)
    assert media["recording"] is None and media["recording_api_ok"] is False


def test_stored_media_from_before_the_check_is_looked_up_again(app, db_session, monkeypatch):
    # Rows saved before the ownership check carry no bound_to; the meeting page must re-check them.
    rec = MeetingRecord(owner_email=OWNER, event_id="evt-z", subject="Customer call",
                        start_utc=datetime(2026, 10, 20, 10), end_utc=datetime(2026, 10, 20, 11),
                        join_url=f"https://zoom.us/j/{MID}", has_transcript=True, has_recording=True,
                        media_json=json.dumps(meeting_sync._zoom_media_from_recording(
                            _recording("bob@cloudfuze.com"))),
                        first_seen_at=datetime(2026, 10, 1), updated_at=datetime(2026, 10, 1))
    db.session.add(rec)
    db.session.commit()
    monkeypatch.setattr(meeting_sync, "zoom_client_or_none",
                        lambda: FakeZoom("bob@cloudfuze.com", ["bob@cloudfuze.com"]))
    from app.routes import _zoom_media_and_transcript
    media, text, _ = _zoom_media_and_transcript(rec, _meeting())
    assert media["recording"] is None and text == ""
    assert json.loads(rec.media_json)["recording"] is None


def test_player_never_serves_unchecked_stored_zoom_media(app, db_session):
    # Even if the re-check did not run, stored media without bound_to is not played.
    rec = MeetingRecord(owner_email=OWNER, event_id="evt-z2", subject="Customer call",
                        start_utc=datetime(2026, 10, 20, 10), end_utc=datetime(2026, 10, 20, 11),
                        join_url=f"https://zoom.us/j/{MID}", has_transcript=True, has_recording=True,
                        media_json=json.dumps(meeting_sync._zoom_media_from_recording(
                            _recording("bob@cloudfuze.com"))),
                        first_seen_at=datetime(2026, 10, 1), updated_at=datetime(2026, 10, 1))
    db.session.add(rec)
    db.session.commit()
    from app.routes import _recording_for_meeting
    with app.test_request_context("/"):
        from flask import session
        session["user"] = {"preferred_username": OWNER}
        assert _recording_for_meeting({"id": "evt-z2"}, app_client=None) is None
        assert "evt-z2" not in session.get("recording_map", {})


def test_participant_lookups_are_cached_per_recording(db_session):
    calls = {"n": 0}

    class CountingZoom(FakeZoom):
        def get_past_meeting_participants(self, uuid):
            calls["n"] += 1
            return None  # Zoom can't say: must not be asked again on every page view

    zoom = CountingZoom("bob@cloudfuze.com", None)
    meeting_sync.lookup_zoom_media(zoom, _meeting(), OWNER)
    meeting_sync.lookup_zoom_media(zoom, _meeting(), OWNER)
    assert calls["n"] == 1
