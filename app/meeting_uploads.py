"""
Manually uploaded videos and transcripts for stored meetings (MeetingRecord).

Used when Teams has no recording/transcript for a meeting (not recorded, client-hosted,
channel recording out of reach, client-hosted Zoom). Videos are written to a private
folder (Config.UPLOAD_DIR) under a generated name — never a user-supplied path — and are
served only through an authorized route. Transcript text is stored in the database.
"""
import logging
import os
import uuid

from app import db
from app.models import MeetingRecord, MeetingUpload
from config import Config

logger = logging.getLogger(__name__)

# Extension -> (content type, magic-byte check). Only browser-playable formats.
_VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}
TRANSCRIPT_FORMATS = {".vtt": "vtt", ".txt": "txt", ".docx": "docx"}
_CHUNK = 1024 * 1024


class UploadError(ValueError):
    """User-facing upload problem (wrong type, too large, empty)."""


def _looks_like_video(head: bytes, ext: str) -> bool:
    """Check the file's own bytes, not just its name (ISO-BMFF 'ftyp' or WebM/EBML)."""
    if ext == ".webm":
        return head[:4] == b"\x1a\x45\xdf\xa3"
    return len(head) >= 12 and head[4:8] == b"ftyp"


def _upload_dir() -> str:
    path = os.path.join(Config.UPLOAD_DIR, "videos")
    os.makedirs(path, exist_ok=True)
    return path


def video_path(upload: MeetingUpload) -> str:
    """Absolute path of a stored video (stored_name is always our own generated name)."""
    return os.path.join(_upload_dir(), os.path.basename(upload.stored_name or ""))


def save_video(record: MeetingRecord, file_storage, uploaded_by: str) -> MeetingUpload:
    """Validate and store an uploaded video; replaces any earlier uploaded video."""
    original = os.path.basename(file_storage.filename or "")
    ext = os.path.splitext(original.lower())[1]
    if ext not in _VIDEO_TYPES:
        raise UploadError("Please upload an .mp4, .webm or .mov video.")

    head = file_storage.stream.read(16)
    if not _looks_like_video(head, ext):
        raise UploadError("This file doesn't look like a valid video.")

    stored_name = f"{record.id}-{uuid.uuid4().hex}{ext}"
    dest = os.path.join(_upload_dir(), stored_name)
    size = 0
    try:
        with open(dest, "wb") as out:
            out.write(head)
            size = len(head)
            while True:
                chunk = file_storage.stream.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > Config.VIDEO_UPLOAD_MAX_BYTES:
                    raise UploadError(
                        f"Video is too large (max {Config.VIDEO_UPLOAD_MAX_BYTES // (1024 * 1024)} MB)."
                    )
                out.write(chunk)
    except Exception:
        if os.path.exists(dest):
            os.remove(dest)
        raise

    _delete_existing(record, "video")
    upload = MeetingUpload(
        meeting_record_id=record.id, kind="video", original_name=original[:255],
        stored_name=stored_name, content_type=_VIDEO_TYPES[ext], size_bytes=size,
        uploaded_by=uploaded_by,
    )
    db.session.add(upload)
    db.session.commit()
    logger.info("Video uploaded for record %s by %s (%d bytes)", record.id, uploaded_by, size)
    return upload


def save_transcript(record: MeetingRecord, original_name: str, fmt: str, text: str,
                    uploaded_by: str) -> MeetingUpload:
    """Store an uploaded transcript (raw text; VTT keeps its timestamps)."""
    if not (text or "").strip():
        raise UploadError("No text could be read from this file.")
    _delete_existing(record, "transcript")
    upload = MeetingUpload(
        meeting_record_id=record.id, kind="transcript",
        original_name=os.path.basename(original_name or "")[:255],
        content_type="text/plain", size_bytes=len(text.encode("utf-8")),
        transcript_text=text, transcript_format=fmt, uploaded_by=uploaded_by,
    )
    db.session.add(upload)
    db.session.commit()
    logger.info("Transcript uploaded for record %s by %s (%s)", record.id, uploaded_by, fmt)
    return upload


def latest(record: MeetingRecord | None, kind: str) -> MeetingUpload | None:
    if record is None:
        return None
    return (
        MeetingUpload.query.filter_by(meeting_record_id=record.id, kind=kind)
        .order_by(MeetingUpload.uploaded_at.desc())
        .first()
    )


def _delete_existing(record: MeetingRecord, kind: str) -> None:
    """Replace semantics: one uploaded video and one uploaded transcript per meeting."""
    for old in MeetingUpload.query.filter_by(meeting_record_id=record.id, kind=kind).all():
        if old.stored_name:
            try:
                os.remove(video_path(old))
            except OSError:
                pass
        db.session.delete(old)
    db.session.flush()
