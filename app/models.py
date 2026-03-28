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
