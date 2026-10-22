"""Activity aggregates behind the dashboard, admin and audit email."""
from datetime import datetime, timedelta

from app import activity_tracker as at
from app import db
from app.models import MeetingAccess, MOMSent
from tests.conftest import make_user


def _seed():
    users = [make_user(f"u{i}@cloudfuze.com", f"U{i}") for i in range(4)]
    db.session.add_all(users)
    db.session.flush()
    t = datetime(2026, 9, 1)
    for k, u in enumerate(users):
        for d in range(6):
            db.session.add(MeetingAccess(user_id=u.id, subject=f"Call {d}", meeting_date=f"2026-09-0{d + 1}",
                                         accessed_at=t + timedelta(hours=k * 10 + d)))
        # Each user sent some; others sending the same meeting must not hide this user's pending one
        for d in range(k):
            db.session.add(MOMSent(user_id=u.id, subject=f"Call {d}", meeting_date=f"2026-09-0{d + 1}",
                                   sent_to="c@x.com"))
    db.session.commit()
    return users


def test_pending_for_one_user_matches_all_users_filtered(db_session):
    users = _seed()
    everyone = at.get_pending_moms()
    for u in users:
        mine = at.get_pending_moms(user_id=u.id)
        expected = [r for r in everyone if r.user_email == u.email]
        assert [tuple(r) for r in mine] == [tuple(r) for r in expected]
    assert len(at.get_pending_moms(user_id=users[3].id)) == 3


def test_user_overview_counts(db_session):
    users = _seed()
    o = at.get_user_overview(users[2].email)
    assert (o["opened"], o["sent"], o["pending"], o["completion"]) == (6, 2, 4, 33)
    assert [p.subject for p in o["pending_list"]] == ["Call 5", "Call 4", "Call 3", "Call 2"]
