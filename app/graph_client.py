import base64
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote
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

    def get_event(self, event_id):
        """Fetch a single calendar event directly by its ID."""
        resp = requests.get(
            f"{self.base_url}/me/events/{event_id}",
            headers=self.headers,
            params={
                "$select": "id,subject,start,end,attendees,onlineMeeting,organizer,isOnlineMeeting"
            },
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

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
        if resp.status_code != 200:
            logger.warning(
                "get_online_meeting_by_join_url: %d - %s",
                resp.status_code,
                resp.text[:200],
            )
            return None
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
        if resp.status_code != 200:
            logger.warning(
                "get_online_meeting_for_user: %d - %s",
                resp.status_code, resp.text[:200],
            )
            return None
        meetings = resp.json().get("value", [])
        return meetings[0] if meetings else None

    def list_transcripts(self, meeting_id, user_id=None):
        """List available transcripts for a given online meeting."""
        if user_id:
            url = f"{self.base_url}/users/{user_id}/onlineMeetings/{meeting_id}/transcripts"
        else:
            url = f"{self.base_url}/me/onlineMeetings/{meeting_id}/transcripts"
        resp = requests.get(url, headers=self.headers)
        if resp.status_code != 200:
            logger.warning("list_transcripts: %d - %s", resp.status_code, resp.text[:200])
            return []
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
        Returns the transcript as plain text (vtt format), or None on failure.
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
        if resp.status_code != 200:
            logger.warning("get_transcript_content: %d - %s", resp.status_code, resp.text[:200])
            return None
        return resp.text

    def send_email(self, to_emails, subject, body_html, attachments, cc_emails=None):
        """
        Send an email with one or more file attachments via Microsoft Graph.

        Args:
            to_emails:   single string or list — appear in the To field
            cc_emails:   single string or list — appear in the CC field (optional)
            attachments: list of dicts with keys 'bytes', 'filename', 'content_type'
        """
        if isinstance(to_emails, str):
            to_emails = [to_emails]
        if isinstance(cc_emails, str):
            cc_emails = [e.strip() for e in cc_emails.split(",") if e.strip()]
        cc_emails = cc_emails or []

        def _recipients(addrs):
            return [
                {"emailAddress": {"address": addr.strip()}}
                for addr in addrs
                if addr.strip()
            ]

        graph_attachments = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": att["filename"],
                "contentType": att["content_type"],
                "contentBytes": base64.b64encode(att["bytes"]).decode("utf-8"),
            }
            for att in attachments
        ]

        message = {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": body_html,
            },
            "toRecipients": _recipients(to_emails),
            "attachments": graph_attachments,
        }
        if cc_emails:
            message["ccRecipients"] = _recipients(cc_emails)

        payload = {"message": message, "saveToSentItems": "true"}

        resp = requests.post(
            f"{self.base_url}/me/sendMail",
            headers=self.headers,
            json=payload,
        )
        resp.raise_for_status()
        return True

    def send_mail_as_user(self, sender_mailbox: str, to_emails, subject: str, body_html: str):
        """
        Send HTML email from a specific user's mailbox (application token).
        Uses POST /users/{sender}/sendMail — requires Mail.Send (application) and a valid sender.

        Args:
            sender_mailbox: UPN or object ID of the mailbox to send from (e.g. shared mailbox).
            to_emails: str or list of recipient addresses.
        """
        if isinstance(to_emails, str):
            to_emails = [e.strip() for e in to_emails.split(",") if e.strip()]

        recipients = [
            {"emailAddress": {"address": addr.strip()}}
            for addr in to_emails
            if addr.strip()
        ]
        if not recipients:
            raise ValueError("No recipients for send_mail_as_user")

        payload = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML",
                    "content": body_html,
                },
                "toRecipients": recipients,
            },
            "saveToSentItems": "true",
        }

        encoded_sender = quote(sender_mailbox.strip(), safe="")
        url = f"{self.base_url}/users/{encoded_sender}/sendMail"
        resp = requests.post(url, headers=self.headers, json=payload)
        if resp.status_code >= 400:
            logger.error(
                "send_mail_as_user failed: %s %s",
                resp.status_code,
                resp.text[:500],
            )
        resp.raise_for_status()
        return True

    # ── Google Meet calendar + transcript methods ────────────────────────────────

    def search_google_meet_events(self, start_date: str, end_date: str) -> list[dict]:
        """
        Fetch Outlook calendar events in the date range that contain a Google Meet link.
        Checks location.displayName and body.content for meet.google.com URLs.
        Returns raw calendar event dicts with an extra '_google_meet_code' key.
        """
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Prefer": 'outlook.timezone="UTC"',
        }
        params = {
            "startDateTime": f"{start_date}T00:00:00Z",
            "endDateTime": f"{end_date}T23:59:59Z",
            "$select": "id,subject,start,end,attendees,organizer,location,body",
            "$orderby": "start/dateTime desc",
            "$top": 100,
        }
        try:
            resp = requests.get(
                f"{self.base_url}/me/calendarView",
                headers=headers,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            events = resp.json().get("value", [])
        except Exception as e:
            logger.error("search_google_meet_events failed: %s", e)
            return []

        result = []
        for event in events:
            location = ((event.get("location") or {}).get("displayName") or "")
            body_content = ((event.get("body") or {}).get("content") or "")
            combined = location + " " + body_content
            if "meet.google.com" not in combined:
                continue
            m = re.search(r"meet\.google\.com/([a-z]+-[a-z]+-[a-z]+)", combined)
            event["_google_meet_code"] = m.group(1) if m else ""
            result.append(event)

        logger.info("search_google_meet_events: %d Google Meet events found", len(result))
        return result

    # ── Google Meet email-based transcript methods (legacy) ──────────────────

    def search_google_meet_emails(self, from_date: str, to_date: str) -> list[dict]:
        """
        Search the signed-in user's mailbox for emails from Google Meet.
        from_date / to_date are 'YYYY-MM-DD' strings.
        Requires Mail.Read scope.
        Returns list of message summary dicts (no body — call get_email_message for body).
        """
        time_min = f"{from_date}T00:00:00Z"
        time_max = f"{to_date}T23:59:59Z"
        odata_filter = (
            f"from/emailAddress/address eq 'meet-recordings-noreply@google.com'"
            f" and receivedDateTime ge {time_min}"
            f" and receivedDateTime le {time_max}"
        )
        params = {
            "$filter": odata_filter,
            "$select": "id,subject,from,receivedDateTime,bodyPreview,toRecipients,ccRecipients",
            "$orderby": "receivedDateTime desc",
            "$top": 50,
        }
        try:
            resp = requests.get(
                f"{self.base_url}/me/messages",
                headers=self.headers,
                params=params,
                timeout=15,
            )
            if resp.status_code == 403:
                logger.warning(
                    "search_google_meet_emails: 403 — Mail.Read scope may not be consented yet"
                )
                return []
            resp.raise_for_status()
            return resp.json().get("value", [])
        except Exception as e:
            logger.error("search_google_meet_emails failed: %s", e)
            return []

    def get_email_message(self, message_id: str) -> dict | None:
        """
        Fetch a single email message with its full HTML body.
        GET /me/messages/{id}?$select=id,subject,from,receivedDateTime,body,toRecipients,ccRecipients
        Requires Mail.Read scope.
        Returns the message dict or None on failure.
        """
        try:
            resp = requests.get(
                f"{self.base_url}/me/messages/{message_id}",
                headers=self.headers,
                params={
                    "$select": "id,subject,from,receivedDateTime,body,toRecipients,ccRecipients"
                },
                timeout=15,
            )
            if resp.status_code in (403, 404):
                logger.warning("get_email_message %s: HTTP %d", message_id, resp.status_code)
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("get_email_message failed for %s: %s", message_id, e)
            return None

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
        Search OneDrives of the viewed user and all distinct organizers
        for recording files. Only searches organizers (not all attendees)
        to keep response times fast.
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

        logger.info("Recording lookup: searching %d user(s)", len(user_ids_to_search))

        workers = min(len(user_ids_to_search), 4)
        with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
            futures = {pool.submit(self._search_user_recordings, uid): uid
                       for uid in user_ids_to_search}
            all_recordings = []
            for future in as_completed(futures):
                all_recordings.extend(future.result())

        logger.info("Recording lookup: found %d recording(s)", len(all_recordings))
        return all_recordings
