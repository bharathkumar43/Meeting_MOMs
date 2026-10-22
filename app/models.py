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


class MeetingUpload(db.Model):
    """
    A video or transcript uploaded by hand for a stored meeting (used when Teams has none).
    Video bytes live on disk under Config.UPLOAD_DIR; transcript text is kept here.
    """
    __tablename__ = "meeting_uploads"

    id = db.Column(db.Integer, primary_key=True)
    meeting_record_id = db.Column(
        db.Integer, db.ForeignKey("meeting_records.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    kind = db.Column(db.String(20), nullable=False)  # "video" | "transcript"
    original_name = db.Column(db.String(255), nullable=False, default="")
    stored_name = db.Column(db.String(255), nullable=True)  # video file name in UPLOAD_DIR
    content_type = db.Column(db.String(100), nullable=False, default="")
    size_bytes = db.Column(db.BigInteger, nullable=False, default=0)
    transcript_text = db.Column(db.Text, nullable=True)   # raw uploaded transcript
    transcript_format = db.Column(db.String(10), nullable=True)  # "vtt" | "txt" | "docx"
    uploaded_by = db.Column(db.String(255), nullable=False, default="")
    uploaded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<MeetingUpload {self.kind} for record {self.meeting_record_id}>"


class MeetingAttendance(db.Model):
    """
    Whether the meeting's owner (the employee whose calendar it's on) actually joined,
    from the Teams attendance report or Zoom participants. attended None = unknown
    (no report, e.g. client-organized); final = no further re-checks.
    """
    __tablename__ = "meeting_attendance"

    meeting_record_id = db.Column(
        db.Integer, db.ForeignKey("meeting_records.id", ondelete="CASCADE"), primary_key=True,
    )
    attended = db.Column(db.Boolean, nullable=True)
    final = db.Column(db.Boolean, nullable=False, default=False)
    checked_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    record = db.relationship(
        "MeetingRecord",
        backref=db.backref("attendance", uselist=False, lazy="joined",
                           cascade="all, delete-orphan", passive_deletes=True),
    )

    def __repr__(self):
        return f"<MeetingAttendance record={self.meeting_record_id}: {self.attended}>"


class MeetingAnalysis(db.Model):
    """
    AI review of how CloudFuze employees handled a customer meeting (run on demand from the
    meeting page; stored so reopening the page costs nothing).
    """
    __tablename__ = "meeting_analysis"

    meeting_record_id = db.Column(
        db.Integer, db.ForeignKey("meeting_records.id", ondelete="CASCADE"), primary_key=True,
    )
    result_json = db.Column(db.Text, nullable=False)
    model = db.Column(db.String(64), nullable=False, default="")
    input_tokens = db.Column(db.Integer, nullable=False, default=0)
    output_tokens = db.Column(db.Integer, nullable=False, default=0)
    created_by = db.Column(db.String(255), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<MeetingAnalysis record={self.meeting_record_id} by {self.created_by}>"


class UserRole(db.Model):
    """
    Role and reporting line set by an admin: 'admin' | 'manager' | 'employee', plus the
    manager an employee reports to. Emails in Config.ADMIN_EMAILS are always admins;
    Config.MANAGER_EMAILS is only the default until an admin sets a role here.
    """
    __tablename__ = "user_roles"

    email = db.Column(db.String(255), primary_key=True)
    role = db.Column(db.String(20), nullable=False, default="employee")
    manager_email = db.Column(db.String(255), nullable=True, index=True)
    updated_by = db.Column(db.String(255), nullable=False, default="")
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<UserRole {self.email}: {self.role} -> {self.manager_email}>"


class RateLimitCounter(db.Model):
    """
    Requests per (limit name + user/IP, fixed time window). Kept in the database so every
    gunicorn worker shares the same counts; rows older than a day are pruned.
    """
    __tablename__ = "rate_limit_counters"

    key = db.Column(db.String(320), primary_key=True)
    window_start = db.Column(db.BigInteger, primary_key=True)
    count = db.Column(db.Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<RateLimitCounter {self.key} @{self.window_start}: {self.count}>"
