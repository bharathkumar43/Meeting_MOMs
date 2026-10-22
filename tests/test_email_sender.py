"""The MOM email body is HTML: meeting text from calendars and forms must be escaped."""
from app import email_sender


def test_email_body_escapes_title_date_and_greeting(monkeypatch):
    sent = {}

    def _send(self, to, subject, body_html, attachments, cc_emails=None):
        sent.update(subject=subject, body=body_html)

    monkeypatch.setattr(email_sender.GraphClient, "send_email", _send)
    email_sender.send_mom_email(
        "token", "c@client.com",
        meeting_title='Sync <a href="https://evil.example/sow">Updated SOW</a>',
        meeting_date="<img src=x>", attachments=[], greeting_name="<b>Bob</b>")
    body = sent["body"]
    assert "<a href" not in body and "<img" not in body and "<b>Bob" not in body
    assert "&lt;a href=&quot;https://evil.example/sow&quot;&gt;" in body
    # The plain-text subject line stays readable (Graph treats it as text, not HTML)
    assert sent["subject"].startswith('Minutes of Meeting - Sync <a href=')
