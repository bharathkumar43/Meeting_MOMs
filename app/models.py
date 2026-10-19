from datetime import datetime
from app import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False, default="")
    first_login = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    login_count = db.Column(db.Integer, nullable=False, default=0)

    meetings = db.relationship(
        "MeetingAccess",
        backref="user",
        lazy="dynamic",
        order_by="MeetingAccess.accessed_at.desc()",
    )

    sent_moms = db.relationship(
        "MOMSent",
        backref="user",
        lazy="dynamic",
        order_by="MOMSent.sent_at.desc()",
    )

    def __repr__(self):
        return f"<User {self.email}>"


class MeetingAccess(db.Model):
    __tablename__ = "meeting_accesses"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    subject = db.Column(db.String(500), nullable=False, default="")
    meeting_date = db.Column(db.String(20), nullable=False, default="")
    accessed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<MeetingAccess {self.subject} by user_id={self.user_id}>"


class MOMSent(db.Model):
    __tablename__ = "mom_sent"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    subject = db.Column(db.String(500), nullable=False, default="")
    meeting_date = db.Column(db.String(20), nullable=False, default="")
    sent_to = db.Column(db.String(255), nullable=False, default="")
    sent_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<MOMSent {self.subject} to {self.sent_to}>"


class MeetingRecord(db.Model):
    """
    A Teams customer meeting on one user's calendar, synced from Microsoft Graph, plus
    its transcript/recording status. The Teams pages read from here instead of calling
    Graph on every visit; app/meeting_sync.py keeps it fresh.
    """
    __tablename__ = "meeting_records"
    __table_args__ = (
        db.UniqueConstraint("owner_email", "event_id", name="uq_meeting_owner_event"),
        db.Index("ix_meeting_owner_start", "owner_email", "start_utc"),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_email = db.Column(db.String(255), nullable=False)
    event_id = db.Column(db.String(512), nullable=False)
    subject = db.Column(db.String(500), nullable=False, default="")
    start_utc = db.Column(db.DateTime, nullable=False)
    end_utc = db.Column(db.DateTime, nullable=True)
    organizer_name = db.Column(db.String(255), nullable=False, default="")
    organizer_email = db.Column(db.String(255), nullable=False, default="")
    join_url = db.Column(db.Text, nullable=False, default="")
    external_attendees_json = db.Column(db.Text, nullable=False, default="[]")
    # None = not checked yet
    has_transcript = db.Column(db.Boolean, nullable=True)
    has_recording = db.Column(db.Boolean, nullable=True)
    # Graph lookup result: owner user ID, onlineMeeting ID, recording entry
    media_json = db.Column(db.Text, nullable=True)
    status_checked_at = db.Column(db.DateTime, nullable=True)
    first_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<MeetingRecord {self.subject} for {self.owner_email}>"


class CalendarSync(db.Model):
    """Per-user sync bookkeeping for MeetingRecord (when, how far back, who is syncing)."""
    __tablename__ = "calendar_syncs"

    owner_email = db.Column(db.String(255), primary_key=True)
    last_synced_at = db.Column(db.DateTime, nullable=True)
    # Earliest date the history backfill has reached
    synced_from = db.Column(db.DateTime, nullable=True)
    # Set while a sync runs; acts as a lock across workers
    sync_started_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.String(500), nullable=False, default="")

    def __repr__(self):
        return f"<CalendarSync {self.owner_email} at {self.last_synced_at}>"
