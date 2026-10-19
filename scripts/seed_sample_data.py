"""
Seed the local database with sample users, meeting accesses and sent MOMs.

All sample users use the @example.com domain so they never collide with real
accounts. Re-running the script deletes the previous sample rows first.

Usage:
    python scripts/seed_sample_data.py          # reset + seed
    python scripts/seed_sample_data.py --clear  # only remove sample rows
"""
import argparse
import logging
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402
from app.models import MeetingAccess, MOMSent, User  # noqa: E402

logger = logging.getLogger(__name__)

SAMPLE_DOMAIN = "@example.com"

SAMPLE_USERS = [
    ("Asha Reddy", "asha.reddy"),
    ("Daniel Brooks", "daniel.brooks"),
    ("Priya Nair", "priya.nair"),
    ("Marcus Lee", "marcus.lee"),
    ("Sofia Martinez", "sofia.martinez"),
    ("Kiran Varma", "kiran.varma"),
]

CUSTOMERS = [
    ("Contoso", "contoso.com"),
    ("Fabrikam", "fabrikam.com"),
    ("Northwind Traders", "northwind.com"),
    ("Tailspin Toys", "tailspintoys.com"),
    ("Adventure Works", "adventure-works.com"),
    ("Woodgrove Bank", "woodgrovebank.com"),
]

TOPICS = [
    "Migration Kickoff",
    "Box to SharePoint Scoping Call",
    "Slack to Teams Migration Review",
    "Weekly Migration Status Sync",
    "Gmail to Exchange Cutover Planning",
    "Delta Migration Walkthrough",
    "Post-Migration Validation",
]


def clear_sample_data() -> int:
    """Delete every row belonging to @example.com users. Returns users removed."""
    users = User.query.filter(User.email.like(f"%{SAMPLE_DOMAIN}")).all()
    ids = [u.id for u in users]
    if ids:
        MOMSent.query.filter(MOMSent.user_id.in_(ids)).delete(synchronize_session=False)
        MeetingAccess.query.filter(MeetingAccess.user_id.in_(ids)).delete(
            synchronize_session=False
        )
        User.query.filter(User.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    return len(ids)


def seed_sample_data(seed: int = 42) -> dict:
    """Insert sample users with ~30 days of meeting/MOM activity."""
    rng = random.Random(seed)
    now = datetime.utcnow()
    counts = {"users": 0, "accesses": 0, "sent": 0}

    for name, local in SAMPLE_USERS:
        first_login = now - timedelta(days=rng.randint(30, 90))
        last_login = now - timedelta(hours=rng.randint(1, 24 * 10))
        user = User(
            email=f"{local}{SAMPLE_DOMAIN}",
            name=name,
            first_login=first_login,
            last_login=last_login,
            login_count=rng.randint(3, 40),
        )
        db.session.add(user)
        db.session.flush()
        counts["users"] += 1

        for _ in range(rng.randint(4, 9)):
            customer, cust_domain = rng.choice(CUSTOMERS)
            subject = f"{customer} | {rng.choice(TOPICS)}"
            meeting_dt = now - timedelta(days=rng.randint(0, 30), hours=rng.randint(0, 8))
            meeting_date = meeting_dt.strftime("%Y-%m-%d")

            # One or two opens per meeting, shortly after it happened
            accessed_at = meeting_dt + timedelta(minutes=rng.randint(30, 240))
            for _ in range(rng.randint(1, 2)):
                db.session.add(MeetingAccess(
                    user_id=user.id,
                    subject=subject,
                    meeting_date=meeting_date,
                    accessed_at=min(accessed_at, now),
                ))
                counts["accesses"] += 1
                accessed_at += timedelta(minutes=rng.randint(5, 120))

            # ~65% of opened meetings get a MOM sent; the rest show as pending
            if rng.random() < 0.65:
                sent_at = min(accessed_at + timedelta(minutes=rng.randint(10, 180)), now)
                for contact in rng.sample(["it.admin", "pm", "cio"], rng.randint(1, 2)):
                    db.session.add(MOMSent(
                        user_id=user.id,
                        subject=subject,
                        meeting_date=meeting_date,
                        sent_to=f"{contact}@{cust_domain}",
                        sent_at=sent_at,
                    ))
                    counts["sent"] += 1

    db.session.commit()
    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--clear", action="store_true", help="only remove sample rows")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        removed = clear_sample_data()
        logger.info(f"Removed {removed} existing sample users")
        if args.clear:
            return
        counts = seed_sample_data()
        logger.info(
            f"Seeded {counts['users']} users, {counts['accesses']} meeting accesses, "
            f"{counts['sent']} MOM-sent rows"
        )


if __name__ == "__main__":
    main()
