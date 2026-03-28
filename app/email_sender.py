from app.graph_client import GraphClient


def send_mom_email(access_token, to_email, meeting_title, meeting_date, doc_bytes):
    """
    Send the MOM Word document to a customer via email.

    Args:
        access_token: Microsoft Graph API access token
        to_email: Customer's email address
        meeting_title: Title of the meeting (used in subject/body)
        meeting_date: Date of the meeting
        doc_bytes: The Word document as bytes
    """
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

    client.send_email(to_email, subject, body_html, doc_bytes, filename)
    return filename
