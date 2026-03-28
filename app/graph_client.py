import base64
import requests
from config import Config


class GraphClient:
    """Wrapper around Microsoft Graph API for meetings, transcripts, and email."""

    def __init__(self, access_token):
        self.access_token = access_token
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        self.base_url = Config.GRAPH_API_BASE

    def get_user_profile(self):
        """Get the signed-in user's profile."""
        resp = requests.get(f"{self.base_url}/me", headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    def list_calendar_events(self, start_date, end_date):
        """
        Fetch calendar events in a date range that are online meetings.
        Uses the calendarView endpoint which supports date-range queries natively.
        Filters for online meetings client-side since $filter doesn't support isOnlineMeeting.
        """
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Prefer": 'outlook.timezone="UTC"',
        }
        params = {
            "startDateTime": f"{start_date}T00:00:00Z",
            "endDateTime": f"{end_date}T23:59:59Z",
            "$select": "id,subject,start,end,attendees,onlineMeeting,organizer,isOnlineMeeting",
            "$orderby": "start/dateTime desc",
            "$top": 100,
        }
        resp = requests.get(
            f"{self.base_url}/me/calendarView",
            headers=headers,
            params=params,
        )
        resp.raise_for_status()
        events = resp.json().get("value", [])

        return [e for e in events if e.get("isOnlineMeeting")]

    def get_online_meeting_by_join_url(self, join_url):
        """Resolve a calendar event's join URL to an onlineMeeting object."""
        params = {
            "$filter": f"JoinWebUrl eq '{join_url}'",
        }
        resp = requests.get(
            f"{self.base_url}/me/onlineMeetings",
            headers=self.headers,
            params=params,
        )
        resp.raise_for_status()
        meetings = resp.json().get("value", [])
        return meetings[0] if meetings else None

    def list_transcripts(self, meeting_id):
        """List available transcripts for a given online meeting."""
        resp = requests.get(
            f"{self.base_url}/me/onlineMeetings/{meeting_id}/transcripts",
            headers=self.headers,
        )
        resp.raise_for_status()
        return resp.json().get("value", [])

    def get_transcript_content(self, meeting_id, transcript_id):
        """
        Fetch the actual transcript text content.
        Returns the transcript as plain text (vtt format).
        """
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "text/vtt",
        }
        resp = requests.get(
            f"{self.base_url}/me/onlineMeetings/{meeting_id}/transcripts/{transcript_id}/content",
            headers=headers,
            params={"$format": "text/vtt"},
        )
        resp.raise_for_status()
        return resp.text

    def send_email(self, to_email, subject, body_html, attachment_bytes, filename):
        """
        Send an email with a Word document attachment via Microsoft Graph.
        """
        attachment_b64 = base64.b64encode(attachment_bytes).decode("utf-8")

        payload = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML",
                    "content": body_html,
                },
                "toRecipients": [
                    {
                        "emailAddress": {
                            "address": to_email,
                        }
                    }
                ],
                "attachments": [
                    {
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": filename,
                        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "contentBytes": attachment_b64,
                    }
                ],
            },
            "saveToSentItems": "true",
        }

        resp = requests.post(
            f"{self.base_url}/me/sendMail",
            headers=self.headers,
            json=payload,
        )
        resp.raise_for_status()
        return True
