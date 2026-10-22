"""The OneDrive recording fallback must only bind a file from the meeting's own people
(owner, or the organizer named in the Microsoft-issued join link) with a real subject match."""
from app import meeting_sync

OWNER_UID = "uid-alice"
ORG_OID = "11111111-1111-1111-1111-111111111111"


def _meeting(subject="Acme quarterly review", oid=ORG_OID):
    link = ("https://teams.microsoft.com/l/meetup-join/x?context="
            "%7b%22Tid%22%3a%22t%22%2c%22Oid%22%3a%22" + oid + "%22%7d") if oid else ""
    return {"subject": subject, "start": {"dateTime": "2026-10-20T10:00:00.0000000"},
            "onlineMeeting": {"joinUrl": link} if link else None}


def _file(subject, user_id, date="20261020"):
    return {"subject": subject, "date": date, "time": "100500", "item_id": "i-" + user_id, "user_id": user_id}


def test_unrelated_users_file_is_not_bound():
    files = [_file("Board sync confidential", "uid-bob")]
    assert meeting_sync.find_recording(_meeting("Sync"), files, OWNER_UID) is None
    assert meeting_sync.find_recording(_meeting(""), files, OWNER_UID) is None


def test_matching_file_from_another_users_drive_is_not_bound():
    files = [_file("Acme quarterly review", "uid-bob")]
    assert meeting_sync.find_recording(_meeting(), files, OWNER_UID) is None


def test_owner_and_link_organizer_files_are_bound():
    assert meeting_sync.find_recording(_meeting(), [_file("Acme quarterly review", OWNER_UID)],
                                       OWNER_UID)["user_id"] == OWNER_UID
    assert meeting_sync.find_recording(_meeting(), [_file("Acme quarterly review | Q4", ORG_OID)],
                                       OWNER_UID, ORG_OID)["user_id"] == ORG_OID


def test_link_oid_alone_is_not_trusted():
    # A join link can be copied into an invite; the organizer must resolve to the same person
    files = [_file("Acme quarterly review", ORG_OID)]
    assert meeting_sync.find_recording(_meeting(), files, OWNER_UID) is None
    assert meeting_sync.find_recording(_meeting(), files, OWNER_UID, "uid-someone-else") is None


def test_short_or_empty_subject_does_not_match_everything():
    files = [_file("Sync with the whole finance team", OWNER_UID)]
    assert meeting_sync.find_recording(_meeting("Sync"), files, OWNER_UID) is None
    assert meeting_sync.find_recording(_meeting(""), files, OWNER_UID) is None
