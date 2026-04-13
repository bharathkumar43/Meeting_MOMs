import base64
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from config import Config

logger = logging.getLogger(__name__)


def _parse_retry_after(resp):
    """Parse Retry-After header safely. Handles values like '30', '30,120', or missing."""
    raw = resp.headers.get("Retry-After", "5")
    try:
        return int(raw.split(",")[0].strip())
    except (ValueError, AttributeError):
        return 5


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
        """Resolve a calendar event's join URL to an onlineMeeting object (delegated)."""
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

    def get_online_meeting_for_user(self, user_id, join_url):
        """Resolve a join URL to an onlineMeeting via a specific user (app-level).
        Requires Application Access Policy for Teams."""
        params = {
            "$filter": f"JoinWebUrl eq '{join_url}'",
        }
        resp = requests.get(
            f"{self.base_url}/users/{user_id}/onlineMeetings",
            headers=self.headers,
            params=params,
        )
        if resp.status_code == 403:
            logger.info("onlineMeetings API requires Application Access Policy")
            return None
        resp.raise_for_status()
        meetings = resp.json().get("value", [])
        return meetings[0] if meetings else None

    def list_transcripts(self, meeting_id, user_id=None):
        """List available transcripts for a given online meeting."""
        if user_id:
            url = f"{self.base_url}/users/{user_id}/onlineMeetings/{meeting_id}/transcripts"
        else:
            url = f"{self.base_url}/me/onlineMeetings/{meeting_id}/transcripts"
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()
        return resp.json().get("value", [])

    def check_transcript_exists(self, join_url):
        """
        Check if a meeting has transcripts available.
        Returns True if at least one transcript exists, False otherwise.
        """
        try:
            online_meeting = self.get_online_meeting_by_join_url(join_url)
            if not online_meeting:
                return False
            transcripts = self.list_transcripts(online_meeting["id"])
            return len(transcripts) > 0
        except Exception as e:
            logger.warning("Transcript check failed for join_url: %s", e)
            return False

    def get_transcript_content(self, meeting_id, transcript_id, user_id=None):
        """
        Fetch the actual transcript text content.
        Returns the transcript as plain text (vtt format).
        """
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "text/vtt",
        }
        if user_id:
            url = f"{self.base_url}/users/{user_id}/onlineMeetings/{meeting_id}/transcripts/{transcript_id}/content"
        else:
            url = f"{self.base_url}/me/onlineMeetings/{meeting_id}/transcripts/{transcript_id}/content"
        resp = requests.get(url, headers=headers, params={"$format": "text/vtt"})
        resp.raise_for_status()
        return resp.text

    def send_email(self, to_emails, subject, body_html, attachment_bytes, filename):
        """
        Send an email with a Word document attachment via Microsoft Graph.
        to_emails can be a single string or a list of email strings.
        """
        if isinstance(to_emails, str):
            to_emails = [to_emails]

        recipients = [
            {"emailAddress": {"address": addr.strip()}}
            for addr in to_emails
            if addr.strip()
        ]

        attachment_b64 = base64.b64encode(attachment_bytes).decode("utf-8")

        payload = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML",
                    "content": body_html,
                },
                "toRecipients": recipients,
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

    # ── App-level methods (for admin monitoring) ─────────────

    def get_user_id(self, user_email):
        """Resolve a user email to their Azure AD user ID."""
        resp = requests.get(
            f"{self.base_url}/users/{user_email}",
            headers=self.headers,
            params={"$select": "id,displayName,userPrincipalName"},
        )
        resp.raise_for_status()
        return resp.json()

    def list_user_calendar_events(self, user_id, start_date, end_date):
        """Fetch calendar events for a specific user (requires app-level token)."""
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
            f"{self.base_url}/users/{user_id}/calendarView",
            headers=headers,
            params=params,
        )
        resp.raise_for_status()
        events = resp.json().get("value", [])
        return [e for e in events if e.get("isOnlineMeeting")]

    def _search_user_recordings(self, user_id):
        """
        Search a user's OneDrive for Teams recording files with retry on 429.
        Teams saves recordings as: Subject-YYYYMMDD_HHMMSS-Meeting Recording.mp4
        Special chars like | are stripped from filenames by the OS.
        """
        for attempt in range(3):
            try:
                resp = requests.get(
                    f"{self.base_url}/users/{user_id}/drive/root/search(q='Meeting Recording')",
                    headers=self.headers,
                    params={"$select": "name,createdDateTime", "$top": 200},
                )
                if resp.status_code == 429:
                    wait = _parse_retry_after(resp)
                    logger.info("Rate limited searching %s, retrying in %ds", user_id, wait)
                    time.sleep(wait)
                    continue
                if resp.status_code != 200:
                    return []

                recordings = []
                for f in resp.json().get("value", []):
                    name = f.get("name", "")
                    match = re.search(
                        r"^(.+?)-(\d{8})_(\d{6})(?:UTC)?-Meeting Recording",
                        name,
                    )
                    if match:
                        recordings.append({
                            "subject": match.group(1).strip(),
                            "date": match.group(2),
                            "time": match.group(3),
                        })
                return recordings
            except Exception as e:
                logger.warning("OneDrive recording search error for %s: %s", user_id, e)
                return []
        return []

    def _resolve_user_id_safe(self, email):
        """Resolve email to user ID, returning None on failure."""
        try:
            resp = requests.get(
                f"{self.base_url}/users/{email}",
                headers=self.headers,
                params={"$select": "id"},
            )
            if resp.status_code == 429:
                wait = _parse_retry_after(resp)
                time.sleep(wait)
                resp = requests.get(
                    f"{self.base_url}/users/{email}",
                    headers=self.headers,
                    params={"$select": "id"},
                )
            if resp.status_code == 200:
                return resp.json().get("id")
        except Exception:
            pass
        return None

    def build_recording_lookup(self, meetings, user_id, org_domain=""):
        """
        Search OneDrives of the viewed user, all organizers, and all internal
        attendees for recording files.  Uses a thread pool with limited
        concurrency to avoid Graph API rate limits.
        """
        if not org_domain:
            org_domain = getattr(Config, "ORG_DOMAIN", "")

        emails_seen = set()
        user_ids_to_search = set()
        user_ids_to_search.add(user_id)

        for m in meetings:
            org_email = (
                m.get("organizer", {}).get("emailAddress", {}).get("address", "")
            ).lower()
            if org_email and org_email.endswith(f"@{org_domain}") and org_email not in emails_seen:
                emails_seen.add(org_email)
                uid = self._resolve_user_id_safe(org_email)
                if uid:
                    user_ids_to_search.add(uid)

            for att in m.get("attendees", []):
                att_email = (
                    att.get("emailAddress", {}).get("address", "")
                ).lower()
                if att_email and att_email.endswith(f"@{org_domain}") and att_email not in emails_seen:
                    emails_seen.add(att_email)
                    uid = self._resolve_user_id_safe(att_email)
                    if uid:
                        user_ids_to_search.add(uid)

        workers = min(len(user_ids_to_search), 4)
        with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
            futures = {pool.submit(self._search_user_recordings, uid): uid
                       for uid in user_ids_to_search}
            all_recordings = []
            for future in as_completed(futures):
                all_recordings.extend(future.result())

        return all_recordings
