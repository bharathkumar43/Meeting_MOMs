from app.graph_client import GraphClient


def send_mom_email(access_token, to_emails, meeting_title, meeting_date, doc_bytes):
    """
    Send the MOM Word document to customers via email.

    Args:
        access_token: Microsoft Graph API access token
        to_emails: Single email string or list of email strings
        meeting_title: Title of the meeting (used in subject/body)
        meeting_date: Date of the meeting
        doc_bytes: The Word document as bytes

    Returns:
        (filename, list_of_emails_sent_to)
    """
    if isinstance(to_emails, str):
        to_emails = [e.strip() for e in to_emails.split(",") if e.strip()]

    client = GraphClient(access_token)

    subject = f"Minutes of Meeting - {meeting_title} ({meeting_date})"

    body_html = f"""
    <div style="font-family: Calibri, sans-serif; color: #333;">
        <p>Dear Customer,</p>
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

    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in meeting_title)
    filename = f"MOM_{safe_title}_{meeting_date}.docx"

    client.send_email(to_emails, subject, body_html, doc_bytes, filename)
    return filename, to_emails
