"""
Build and send the rolling audit report email via Microsoft Graph (app-only).
Requires Mail.Send (application) + AUDIT_SENDER_MAILBOX / AUDIT_REPORT_RECIPIENTS.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime

from app.activity_tracker import get_audit_rows, get_audit_totals
from app.auth import get_app_token
from app.graph_client import GraphClient
from config import Config

logger = logging.getLogger(__name__)


def build_audit_html(rows: list[dict], totals: dict, days: int, generated_at_utc: datetime) -> str:
    """HTML body for the audit email."""
    ts = generated_at_utc.strftime("%Y-%m-%d %H:%M UTC")
    intro = (
        f"<p><strong>Meeting MOM Generator</strong> — rolling <strong>{days}-day</strong> audit "
        f"(users with sign-in during this period).</p>"
        f"<p class='muted'>Generated {html.escape(ts)}</p>"
    )

    summary = (
        "<h3>Summary</h3>"
        "<table style='border-collapse:collapse;margin-bottom:16px;'>"
        "<tr><td style='padding:6px 12px;border:1px solid #ddd;'><strong>Users (logged in)</strong></td>"
        f"<td style='padding:6px 12px;border:1px solid #ddd;'>{totals['users']}</td></tr>"
        "<tr><td style='padding:6px 12px;border:1px solid #ddd;'><strong>Meetings opened</strong></td>"
        f"<td style='padding:6px 12px;border:1px solid #ddd;'>{totals['meetings_opened']}</td></tr>"
        "<tr><td style='padding:6px 12px;border:1px solid #ddd;'><strong>Pending send</strong></td>"
        f"<td style='padding:6px 12px;border:1px solid #ddd;'>{totals['pending']}</td></tr>"
        "<tr><td style='padding:6px 12px;border:1px solid #ddd;'><strong>MOMs sent</strong></td>"
        f"<td style='padding:6px 12px;border:1px solid #ddd;'>{totals['sent']}</td></tr>"
        "</table>"
    )

    thead = (
        "<thead><tr style='background:#f0f4f8;'>"
        "<th style='text-align:left;padding:8px;border:1px solid #ccc;'>Name</th>"
        "<th style='text-align:left;padding:8px;border:1px solid #ccc;'>Email</th>"
        "<th style='text-align:right;padding:8px;border:1px solid #ccc;'>Meetings opened</th>"
        "<th style='text-align:right;padding:8px;border:1px solid #ccc;'>Pending</th>"
        "<th style='text-align:right;padding:8px;border:1px solid #ccc;'>Sent</th>"
        "</tr></thead>"
    )

    body_rows = []
    for r in rows:
        body_rows.append(
            "<tr>"
            f"<td style='padding:8px;border:1px solid #ddd;'>{html.escape(r['name'])}</td>"
            f"<td style='padding:8px;border:1px solid #ddd;'>{html.escape(r['email'])}</td>"
            f"<td style='text-align:right;padding:8px;border:1px solid #ddd;'>{r['meetings_opened']}</td>"
            f"<td style='text-align:right;padding:8px;border:1px solid #ddd;'>{r['pending']}</td>"
            f"<td style='text-align:right;padding:8px;border:1px solid #ddd;'>{r['sent']}</td>"
            "</tr>"
        )

    table = (
        "<h3>By user</h3>"
        "<table style='border-collapse:collapse;width:100%;max-width:960px;font-size:14px;'>"
        f"{thead}<tbody>{''.join(body_rows)}</tbody></table>"
        if rows
        else "<p><em>No users logged in during this period.</em></p>"
    )

    wrapper = (
        "<div style='font-family:Calibri,Arial,sans-serif;color:#222;'>"
        f"{intro}{summary}{table}"
        "<hr style='margin-top:24px;border:none;border-top:1px solid #ddd;'/>"
        "<p style='font-size:11px;color:#777;'>Automated message from Meeting MOM Generator audit job.</p>"
        "</div>"
    )
    return wrapper


def send_daily_audit_report(force: bool = False) -> tuple[bool, str]:
    """
    Compose and send the audit email. Intended for cron / CLI with app context.

    Args:
        force: If True (e.g. admin button), send even when AUDIT_REPORT_ENABLED is false.

    Returns:
        (success, message) — message explains skip or error.
    """
    if not force and not Config.AUDIT_REPORT_ENABLED:
        return False, "AUDIT_REPORT_ENABLED is not true — skipped."

    if not Config.AUDIT_SENDER_MAILBOX:
        return False, "AUDIT_SENDER_MAILBOX is not set."

    if not Config.AUDIT_REPORT_RECIPIENTS:
        return False, "AUDIT_REPORT_RECIPIENTS is empty."

    token = get_app_token()
    if not token:
        return False, "Application token unavailable (check Azure credentials and permissions)."

    days = max(1, Config.AUDIT_REPORT_DAYS)
    rows = get_audit_rows(days=days)
    totals = get_audit_totals(rows)
    now = datetime.utcnow()
    body_html = build_audit_html(rows, totals, days, now)

    subject = (
        f"MOM Generator audit ({days}d) — "
        f"{now.strftime('%Y-%m-%d')} UTC — "
        f"{totals['users']} users"
    )

    client = GraphClient(token)
    try:
        client.send_mail_as_user(
            Config.AUDIT_SENDER_MAILBOX,
            Config.AUDIT_REPORT_RECIPIENTS,
            subject,
            body_html,
        )
    except Exception as e:
        logger.exception("send_daily_audit_report failed")
        return False, str(e)

    logger.info(
        "Audit report emailed from %s to %s",
        Config.AUDIT_SENDER_MAILBOX,
        ",".join(Config.AUDIT_REPORT_RECIPIENTS),
    )
    return True, "Sent."
