from datetime import datetime, timedelta
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


def get_pending_moms(user_id: int | None = None):
    """
    Return unique meetings accessed but never sent.
    Groups by (user_id, subject, meeting_date) and keeps only
    the most recent access time — no duplicates.
    Pass user_id to restrict the result to a single user.
    """
    sent_subq = db.session.query(
        MOMSent.user_id,
        MOMSent.subject,
        MOMSent.meeting_date,
    ).distinct().subquery()

    latest_access = func.max(MeetingAccess.accessed_at).label("accessed_at")

    query = (
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
    )
    if user_id is not None:
        query = query.filter(MeetingAccess.user_id == user_id)
    return (
        query.group_by(
            MeetingAccess.user_id,
            MeetingAccess.subject,
            MeetingAccess.meeting_date,
            User.name,
            User.email,
        )
        .order_by(latest_access.desc())
        .all()
    )


def get_user_overview(email: str, recent_limit: int = 8) -> dict:
    """
    Personal activity summary for the signed-in user's overview dashboard.
    Counts are distinct (subject, meeting_date) pairs, matching the admin stats.
    """
    overview = {
        "user": None,
        "opened": 0,
        "sent": 0,
        "pending": 0,
        "opened_week": 0,
        "sent_week": 0,
        "completion": 0,
        "pending_list": [],
        "recent_sent": [],
    }
    user = User.query.filter_by(email=email).first()
    if not user:
        return overview

    week_ago = datetime.utcnow() - timedelta(days=7)

    def distinct_count(model, since=None) -> int:
        query = db.session.query(model.subject, model.meeting_date).filter(
            model.user_id == user.id
        )
        if since is not None:
            time_col = model.accessed_at if model is MeetingAccess else model.sent_at
            query = query.filter(time_col >= since)
        return query.distinct().count()

    pending_rows = get_pending_moms(user_id=user.id)

    # MOMSent stores one row per recipient — group them into one entry per MOM
    grouped: dict = {}
    sent_rows = (
        MOMSent.query.filter_by(user_id=user.id)
        .order_by(MOMSent.sent_at.desc())
        .limit(recent_limit * 6)
        .all()
    )
    for row in sent_rows:
        key = (row.subject, row.meeting_date)
        if key not in grouped:
            if len(grouped) >= recent_limit:
                continue
            grouped[key] = {
                "subject": row.subject,
                "meeting_date": row.meeting_date,
                "sent_at": row.sent_at,
                "recipients": [],
            }
        if row.sent_to not in grouped[key]["recipients"]:
            grouped[key]["recipients"].append(row.sent_to)

    opened = distinct_count(MeetingAccess)
    sent = distinct_count(MOMSent)
    overview.update({
        "user": user,
        "opened": opened,
        "sent": sent,
        "pending": len(pending_rows),
        "opened_week": distinct_count(MeetingAccess, week_ago),
        "sent_week": distinct_count(MOMSent, week_ago),
        "completion": min(100, round(100 * sent / opened)) if opened else 0,
        "pending_list": pending_rows[:recent_limit],
        "recent_sent": list(grouped.values()),
    })
    return overview


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


def get_all_user_dashboard_stats():
    """All-time per-user stats for the admin overview dashboard."""
    manager_emails = {e.lower() for e in (Config.MANAGER_EMAILS or [])}
    users = User.query.order_by(User.last_login.desc()).all()
    if not users:
        return []

    # Distinct (user_id, subject, meeting_date) accessed — all time
    access_subq = (
        db.session.query(
            MeetingAccess.user_id.label("uid"),
            MeetingAccess.subject.label("subj"),
            MeetingAccess.meeting_date.label("md"),
        )
        .distinct()
        .subquery("access_subq")
    )
    meetings_counts = dict(
        db.session.query(access_subq.c.uid, func.count())
        .group_by(access_subq.c.uid)
        .all()
    )

    # Distinct sent — all time
    sent_subq = (
        db.session.query(
            MOMSent.user_id.label("uid"),
            MOMSent.subject.label("subj"),
            MOMSent.meeting_date.label("md"),
        )
        .distinct()
        .subquery("sent_subq")
    )
    sent_counts = dict(
        db.session.query(sent_subq.c.uid, func.count())
        .group_by(sent_subq.c.uid)
        .all()
    )

    # Pending = accessed but no matching sent row
    acc_pend = (
        db.session.query(
            MeetingAccess.user_id.label("uid"),
            MeetingAccess.subject.label("subj"),
            MeetingAccess.meeting_date.label("md"),
        )
        .distinct()
        .subquery("acc_pend")
    )
    snt_pend = (
        db.session.query(
            MOMSent.user_id.label("uid"),
            MOMSent.subject.label("subj"),
            MOMSent.meeting_date.label("md"),
        )
        .distinct()
        .subquery("snt_pend")
    )
    pending_counts = dict(
        db.session.query(acc_pend.c.uid, func.count())
        .outerjoin(
            snt_pend,
            db.and_(
                snt_pend.c.uid == acc_pend.c.uid,
                snt_pend.c.subj == acc_pend.c.subj,
                snt_pend.c.md == acc_pend.c.md,
            ),
        )
        .filter(snt_pend.c.uid.is_(None))
        .group_by(acc_pend.c.uid)
        .all()
    )

    rows = []
    for u in users:
        uid = u.id
        sent = sent_counts.get(uid, 0)
        pending = pending_counts.get(uid, 0)
        rows.append({
            "name": u.name or "",
            "email": u.email,
            "is_manager": u.email.lower() in manager_emails,
            "first_login": u.first_login,
            "last_login": u.last_login,
            "login_count": u.login_count,
            "meetings": meetings_counts.get(uid, 0),
            "sent": sent,
            "pending": pending,
        })
    return rows


def get_audit_rows(days: int = 7):
    """
    Rolling-window audit for users whose last_login falls within the window.

    Per user (among those active):
    - meetings_opened: distinct (subject, meeting_date) with MeetingAccess.accessed_at in window
    - sent: distinct (subject, meeting_date) with MOMSent.sent_at in window
    - pending: distinct meetings opened in window with no MOMSent row for that triple

    Returns:
        list of dicts: name, email, meetings_opened, pending, sent
    """
    since = datetime.utcnow() - timedelta(days=days)

    active_users = (
        User.query.filter(User.last_login >= since)
        .order_by(func.lower(User.name))
        .all()
    )
    if not active_users:
        return []

    # Distinct meetings accessed in window → count per user_id
    access_distinct = (
        db.session.query(
            MeetingAccess.user_id,
            MeetingAccess.subject,
            MeetingAccess.meeting_date,
        )
        .filter(MeetingAccess.accessed_at >= since)
        .distinct()
    ).subquery()

    opened_counts = dict(
        db.session.query(access_distinct.c.user_id, func.count())
        .group_by(access_distinct.c.user_id)
        .all()
    )

    # Distinct meetings sent in window → count per user_id
    sent_distinct = (
        db.session.query(
            MOMSent.user_id,
            MOMSent.subject,
            MOMSent.meeting_date,
        )
        .filter(MOMSent.sent_at >= since)
        .distinct()
    ).subquery()

    sent_counts = dict(
        db.session.query(sent_distinct.c.user_id, func.count())
        .group_by(sent_distinct.c.user_id)
        .all()
    )

    # Pending: accessed in window, no matching sent row (any time)
    access_keys = (
        db.session.query(
            MeetingAccess.user_id.label("uid"),
            MeetingAccess.subject.label("subj"),
            MeetingAccess.meeting_date.label("md"),
        )
        .filter(MeetingAccess.accessed_at >= since)
        .distinct()
    ).subquery()

    sent_keys = (
        db.session.query(
            MOMSent.user_id.label("uid"),
            MOMSent.subject.label("subj"),
            MOMSent.meeting_date.label("md"),
        )
        .distinct()
    ).subquery()

    pending_counts = dict(
        db.session.query(access_keys.c.uid, func.count())
        .outerjoin(
            sent_keys,
            db.and_(
                sent_keys.c.uid == access_keys.c.uid,
                sent_keys.c.subj == access_keys.c.subj,
                sent_keys.c.md == access_keys.c.md,
            ),
        )
        .filter(sent_keys.c.uid.is_(None))
        .group_by(access_keys.c.uid)
        .all()
    )

    rows = []
    for u in active_users:
        uid = u.id
        rows.append({
            "name": u.name or "",
            "email": u.email,
            "meetings_opened": opened_counts.get(uid, 0),
            "pending": pending_counts.get(uid, 0),
            "sent": sent_counts.get(uid, 0),
        })
    return rows


def get_audit_totals(rows):
    """Sum columns for summary line (same keys as get_audit_rows dicts)."""
    if not rows:
        return {"users": 0, "meetings_opened": 0, "pending": 0, "sent": 0}
    return {
        "users": len(rows),
        "meetings_opened": sum(r["meetings_opened"] for r in rows),
        "pending": sum(r["pending"] for r in rows),
        "sent": sum(r["sent"] for r in rows),
    }
