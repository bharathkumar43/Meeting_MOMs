from app.graph_client import GraphClient


def send_mom_email(access_token, to_emails, meeting_title, meeting_date, attachments, cc_emails=None, greeting_name="Customer"):
    """
    Send the MOM document(s) to customers via email.

    Args:
        access_token:  Microsoft Graph API access token
        to_emails:     Single email string or list — appear in the To field
        cc_emails:     Single email string or list — appear in the CC field (optional)
        meeting_title: Title of the meeting (used in subject/body)
        meeting_date:  Date of the meeting
        attachments:   List of dicts with keys 'bytes', 'filename', 'content_type'

    Returns:
        (primary_filename, list_of_to_emails_sent_to)
    """
    if isinstance(to_emails, str):
        to_emails = [e.strip() for e in to_emails.split(",") if e.strip()]
    if isinstance(cc_emails, str):
        cc_emails = [e.strip() for e in cc_emails.split(",") if e.strip()]

    client = GraphClient(access_token)

    subject = f"Minutes of Meeting - {meeting_title} ({meeting_date})"

    body_html = f"""
    <div style="font-family: Calibri, sans-serif; color: #333;">
        <p>Dear {greeting_name},</p>
        <p>Please find attached the Minutes of Meeting for the following session:</p>
        <table style="border-collapse: collapse; margin: 16px 0;">
            <tr>
                <td style="padding: 8px 16px; font-weight: bold; background: #f0f4f8;">Meeting</td>
                <td style="padding: 8px 16px; background: #f0f4f8;">{meeting_title}</td>
            </tr>
            <tr>
                <td style="padding: 8px 16px; font-weight: bold;">Date</td>
                <td style="padding: 8px 16px;">{meeting_date}</td>
            </tr>
        </table>
        <p>If you have any questions or need clarification on any points discussed,
        please don't hesitate to reach out.</p>
        <p>Best regards,<br>CloudFuze Migration Team</p>
        <hr style="border: none; border-top: 1px solid #ddd; margin-top: 24px;">
        <p style="font-size: 11px; color: #999;">
            This email and its attachments are confidential and intended solely for the
            addressed recipient(s). If you received this in error, please notify the sender
            and delete this email.
        </p>
    </div>
    """

    client.send_email(to_emails, subject, body_html, attachments, cc_emails=cc_emails)
    primary_filename = attachments[0]["filename"] if attachments else ""
    return primary_filename, to_emails
