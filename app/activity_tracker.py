from datetime import datetime
from app import db
from app.models import User, MeetingAccess


def record_login(email: str, name: str):
    """Create or update a user record on each login."""
    user = User.query.filter_by(email=email).first()
    now = datetime.utcnow()
    if user:
        user.name = name
        user.last_login = now
        user.login_count += 1
    else:
        user = User(
            email=email,
            name=name,
            first_login=now,
            last_login=now,
            login_count=1,
        )
        db.session.add(user)
    db.session.commit()


def record_meeting_access(email: str, subject: str, meeting_date: str):
    """Record that a user opened a meeting transcript."""
    user = User.query.filter_by(email=email).first()
    if not user:
        return
    access = MeetingAccess(
        user_id=user.id,
        subject=subject,
        meeting_date=meeting_date,
        accessed_at=datetime.utcnow(),
    )
    db.session.add(access)
    db.session.commit()


def get_all_users():
    """Return all users ordered by last login descending."""
    return User.query.order_by(User.last_login.desc()).all()


def get_user_stats():
    """Return aggregate stats for the admin summary cards."""
    total_users = User.query.count()
    total_meetings = MeetingAccess.query.count()
    return total_users, total_meetings
