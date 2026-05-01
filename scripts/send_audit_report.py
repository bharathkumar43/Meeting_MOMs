"""
Send the daily rolling audit report email (Graph app-only).

Usage (from project root):
    python scripts/send_audit_report.py

Cron (Linux, daily 08:00 UTC example):
    0 8 * * * cd /path/to/Meeting_MOMs && /path/to/venv/bin/python scripts/send_audit_report.py >> /var/log/mom_audit.log 2>&1

Requires: AUDIT_REPORT_ENABLED=true, AUDIT_SENDER_MAILBOX, AUDIT_REPORT_RECIPIENTS,
Azure app registration with Mail.Send (application) admin-consented.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> None:
    from app import create_app
    from app.audit_report_email import send_daily_audit_report

    app = create_app()
    with app.app_context():
        ok, msg = send_daily_audit_report(force=False)
        print(msg)
        if not ok and "skipped" in msg.lower():
            sys.exit(0)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
