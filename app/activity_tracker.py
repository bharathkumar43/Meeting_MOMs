from datetime import datetime
from sqlalchemy import func
from app import db
from app.models import User, MeetingAccess, MOMSent
from config import Config


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


def record_mom_sent(email: str, subject: str, meeting_date: str, sent_to: str):
    """Record that a MOM was emailed to a customer."""
    user = User.query.filter_by(email=email).first()
    if not user:
        return
    sent = MOMSent(
        user_id=user.id,
        subject=subject,
        meeting_date=meeting_date,
        sent_to=sent_to,
        sent_at=datetime.utcnow(),
    )
    db.session.add(sent)
    db.session.commit()


def get_all_users():
    """Return all users ordered by last login descending."""
    return User.query.order_by(User.last_login.desc()).all()


def get_managers():
    """Return users whose email is in MANAGER_EMAILS, ordered by last login."""
    if not Config.MANAGER_EMAILS:
        return []
    return (
        User.query
        .filter(User.email.in_(Config.MANAGER_EMAILS))
        .order_by(User.last_login.desc())
        .all()
    )


def get_non_managers():
    """Return users whose email is NOT in MANAGER_EMAILS."""
    if not Config.MANAGER_EMAILS:
        return User.query.order_by(User.last_login.desc()).all()
    return (
        User.query
        .filter(~User.email.in_(Config.MANAGER_EMAILS))
        .order_by(User.last_login.desc())
        .all()
    )


def get_user_stats():
    """Return aggregate stats for the admin summary cards."""
    total_users = User.query.count()
    total_meetings = (
        db.session.query(
            MeetingAccess.user_id,
            MeetingAccess.subject,
            MeetingAccess.meeting_date,
        )
        .distinct()
        .count()
    )
    total_sent = MOMSent.query.count()
    return total_users, total_meetings, total_sent


def get_pending_moms():
    """
    Return unique meetings accessed but never sent.
    Groups by (user_id, subject, meeting_date) and keeps only
    the most recent access time — no duplicates.
    """
    sent_subq = db.session.query(
        MOMSent.user_id,
        MOMSent.subject,
        MOMSent.meeting_date,
    ).distinct().subquery()

    latest_access = func.max(MeetingAccess.accessed_at).label("accessed_at")

    pending = (
        db.session.query(
            MeetingAccess.subject,
            MeetingAccess.meeting_date,
            latest_access,
            User.name.label("user_name"),
            User.email.label("user_email"),
        )
        .join(User, User.id == MeetingAccess.user_id)
        .outerjoin(
            sent_subq,
            db.and_(
                sent_subq.c.user_id == MeetingAccess.user_id,
                sent_subq.c.subject == MeetingAccess.subject,
                sent_subq.c.meeting_date == MeetingAccess.meeting_date,
            ),
        )
        .filter(sent_subq.c.user_id.is_(None))
        .group_by(
            MeetingAccess.user_id,
            MeetingAccess.subject,
            MeetingAccess.meeting_date,
            User.name,
            User.email,
        )
        .order_by(latest_access.desc())
        .all()
    )
    return pending


def get_sent_moms():
    """Return all sent MOMs with user info, ordered by most recent."""
    return (
        db.session.query(
            MOMSent.subject,
            MOMSent.meeting_date,
            MOMSent.sent_to,
            MOMSent.sent_at,
            User.name.label("user_name"),
            User.email.label("user_email"),
        )
        .join(User, User.id == MOMSent.user_id)
        .order_by(MOMSent.sent_at.desc())
        .all()
    )
