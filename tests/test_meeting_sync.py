"""Calendar upsert and helpers in app/meeting_sync.py."""
from datetime import datetime, timedelta

from app import db, meeting_sync
from app.models import MeetingRecord, MeetingUpload

OWNER = "emp@cloudfuze.com"
WINDOW = (datetime(2026, 9, 1), datetime(2026, 10, 15, 23, 59, 59))


def _meeting(i: int, subject: str = "", start: datetime | None = None) -> dict:
    s = start or datetime(2026, 9, 10, 8) + timedelta(hours=3 * i)
    return {
        "id": f"evt-{i}", "subject": subject or f"Customer sync {i}",
        "start": {"dateTime": s.strftime("%Y-%m-%dT%H:%M:%S.0000000")},
        "end": {"dateTime": (s + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.0000000")},
        "organizer": {"emailAddress": {"name": "Org", "address": OWNER}},
        "onlineMeeting": {"joinUrl": f"https://teams.microsoft.com/l/meetup-join/{i}"},
        "external_attendees": [{"name": "Client", "email": "c@client.com"}],
    }


def _rows():
    return {r.event_id: r for r in MeetingRecord.query.filter_by(owner_email=OWNER)}


def test_first_sync_inserts_every_meeting(db_session, count_queries):
    meetings = [_meeting(i) for i in range(20)]
    with count_queries() as n:
        result = meeting_sync._upsert_window(OWNER, meetings, *WINDOW)
    assert result == {"added": 20, "updated": 0, "removed": 0}
    assert len(_rows()) == 20
    # Lookups are batched: 1 window query + 1 lookup + inserts + commit, not 1 per meeting
    assert n["count"] <= 20 + 5


def test_changed_join_url_resets_media_status(db_session):
    meeting_sync._upsert_window(OWNER, [_meeting(1)], *WINDOW)
    rec = _rows()["evt-1"]
    rec.has_transcript, rec.media_json = True, '{"x": 1}'
    db.session.commit()
    moved = dict(_meeting(1), onlineMeeting={"joinUrl": "https://teams.microsoft.com/l/other"})
    result = meeting_sync._upsert_window(OWNER, [moved], *WINDOW)
    rec = _rows()["evt-1"]
    assert result["updated"] == 1
    assert rec.has_transcript is None and rec.media_json is None


def test_meeting_moved_into_window_is_updated_not_duplicated(db_session):
    db.session.add(MeetingRecord(owner_email=OWNER, event_id="evt-2", subject="old",
                                 start_utc=datetime(2026, 1, 1), join_url="https://teams.microsoft.com/x"))
    db.session.commit()
    result = meeting_sync._upsert_window(OWNER, [_meeting(2)], *WINDOW)
    assert result == {"added": 0, "updated": 1, "removed": 0}
    assert _rows()["evt-2"].subject == "Customer sync 2"


def test_event_returned_twice_is_stored_once(db_session):
    result = meeting_sync._upsert_window(
        OWNER, [_meeting(3), dict(_meeting(3), subject="Second copy")], *WINDOW)
    assert result == {"added": 1, "updated": 1, "removed": 0}
    assert _rows()["evt-3"].subject == "Second copy"


def test_meeting_gone_from_calendar_is_removed_unless_uploaded(db_session):
    meeting_sync._upsert_window(OWNER, [_meeting(4), _meeting(5)], *WINDOW)
    keep = _rows()["evt-5"]
    db.session.add(MeetingUpload(meeting_record_id=keep.id, kind="transcript",
                                 transcript_text="x", transcript_format="txt"))
    db.session.commit()
    result = meeting_sync._upsert_window(OWNER, [], *WINDOW)
    assert result["removed"] == 1
    assert set(_rows()) == {"evt-5"}


def test_classify_event_detects_zoom_links_and_teams():
    teams = {"isOnlineMeeting": True, "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/x"}}
    zoom = {"isOnlineMeeting": False, "bodyPreview": "Join https://cloudfuze.zoom.us/j/123456789?pwd=a"}
    assert meeting_sync.classify_event(teams)[0] == "teams"
    assert meeting_sync.classify_event(zoom) == ("zoom", "https://cloudfuze.zoom.us/j/123456789?pwd=a")
    assert meeting_sync.classify_event({"isOnlineMeeting": False}) is None


def test_for_occurrence_keeps_only_this_slot():
    meeting = {"start": {"dateTime": "2026-10-01T10:00:00"}, "end": {"dateTime": "2026-10-01T11:00:00"}}
    items = [{"id": "a", "createdDateTime": "2026-10-01T11:30:00Z"},
             {"id": "b", "createdDateTime": "2026-09-24T11:30:00Z"},
             {"id": "c", "createdDateTime": "2026-10-01T12:00:00Z"}]
    assert [i["id"] for i in meeting_sync.for_occurrence(items, meeting)] == ["c", "a"]


def test_due_users_skips_recent_locked_and_non_org(db_session, count_queries):
    from app.models import CalendarSync
    now = datetime.utcnow()
    db.session.add_all([
        CalendarSync(owner_email="never@cloudfuze.com", last_error=""),
        CalendarSync(owner_email="old@cloudfuze.com", last_synced_at=now - timedelta(hours=1), last_error=""),
        CalendarSync(owner_email="fresh@cloudfuze.com", last_synced_at=now - timedelta(minutes=2), last_error=""),
        CalendarSync(owner_email="busy@cloudfuze.com", last_synced_at=now - timedelta(hours=1),
                     sync_started_at=now - timedelta(minutes=1), last_error=""),
        CalendarSync(owner_email="stuck@cloudfuze.com", last_synced_at=now - timedelta(hours=1),
                     sync_started_at=now - timedelta(hours=1), last_error=""),
        CalendarSync(owner_email="guest@other.com", last_error=""),
    ])
    db.session.commit()
    with count_queries() as n:
        due = meeting_sync.due_users()
    assert sorted(due) == ["never@cloudfuze.com", "old@cloudfuze.com", "stuck@cloudfuze.com"]
    assert n["count"] == 1
    assert sorted(meeting_sync.enrolled_users()) == sorted(
        ["never@cloudfuze.com", "old@cloudfuze.com", "fresh@cloudfuze.com",
         "busy@cloudfuze.com", "stuck@cloudfuze.com"])
