"""Manual uploads: replace semantics and file cleanup."""
import io
import os
from datetime import datetime

import pytest
from werkzeug.datastructures import FileStorage

from app import db, meeting_uploads
from app.models import MeetingRecord, MeetingUpload

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def _record():
    rec = MeetingRecord(owner_email="emp@cloudfuze.com", event_id="e1", subject="S",
                        start_utc=datetime(2026, 10, 1), join_url="https://teams.microsoft.com/x")
    db.session.add(rec)
    db.session.commit()
    return rec


def _video(name="a.mp4", data=MP4):
    return FileStorage(stream=io.BytesIO(data), filename=name)


def test_replacing_a_video_removes_the_old_file(db_session):
    rec = _record()
    first = meeting_uploads.save_video(rec, _video(), "emp@cloudfuze.com")
    old_path = meeting_uploads.video_path(first)
    second = meeting_uploads.save_video(rec, _video("b.mp4"), "emp@cloudfuze.com")
    assert not os.path.exists(old_path)
    assert os.path.exists(meeting_uploads.video_path(second))
    assert MeetingUpload.query.filter_by(kind="video").count() == 1


def test_failed_commit_keeps_the_old_file(db_session, monkeypatch):
    rec = _record()
    first = meeting_uploads.save_video(rec, _video(), "emp@cloudfuze.com")
    old_path = meeting_uploads.video_path(first)

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(db.session, "commit", boom)
    with pytest.raises(RuntimeError):
        meeting_uploads.save_video(rec, _video("b.mp4"), "emp@cloudfuze.com")
    assert os.path.exists(old_path)
    assert os.listdir(os.path.dirname(old_path)) == [os.path.basename(old_path)]


def test_rejects_non_video_bytes(db_session):
    rec = _record()
    with pytest.raises(meeting_uploads.UploadError):
        meeting_uploads.save_video(rec, _video(data=b"not a video at all"), "x")
