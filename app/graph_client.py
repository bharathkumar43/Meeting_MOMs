import base64
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, unquote
import requests

from app.ttl_cache import TTLCache
from config import Config

logger = logging.getLogger(__name__)

# (connect, read) timeout for every Graph call so a stalled request can't hang a worker
_TIMEOUT = (5, 30)
# Dashboard file lookup gives up after this many seconds instead of waiting out throttling
_LOOKUP_BUDGET_SECONDS = 25
# Parallel OneDrive searches — kept low because bursts trigger Graph 429 throttling
_LOOKUP_MAX_WORKERS = 3
# calendarView paging: Graph returns one page per call; follow nextLink up to the cap
_CALENDAR_PAGE_SIZE = 250
_CALENDAR_MAX_EVENTS = 2000
# Per-user OneDrive "Meeting Recording" search results; repeated lookups reuse them
_RECORDING_SEARCH_CACHE = TTLCache(ttl_seconds=300)
_CHANNEL_DRIVE_CACHE = TTLCache(ttl_seconds=6 * 3600)  # team alias -> SharePoint drive ID


def clear_recording_search_cache() -> None:
    """Forget cached OneDrive recording searches (used by the manual Sync button)."""
    _RECORDING_SEARCH_CACHE.clear()


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
        self._uid_cache: dict[str, str | None] = {}

    @staticmethod
    def organizer_id_from_join_url(join_url: str) -> str | None:
        """
        Return the real organizer's Entra object ID ("Oid") from a Teams join link.
        Channel meetings show a group mailbox as the calendar organizer, which can't be
        used for onlineMeeting lookups; the Oid in the link's context is the actual owner.
        """
        if not join_url:
            return None
        match = re.search(r"context=(\{.*?\})", unquote(unquote(join_url)))
        if not match:
            return None
        try:
            oid = json.loads(match.group(1)).get("Oid", "")
        except ValueError:
            return None
        # Only accept a GUID — it is placed into a URL path later
        if re.fullmatch(r"[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", oid or ""):
            return oid
        return None

    def get_user_profile(self):
        """Get the signed-in user's profile."""
        resp = requests.get(f"{self.base_url}/me", headers=self.headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def _calendar_view_all(self, url: str, params: dict, headers: dict,
                           max_events: int = _CALENDAR_MAX_EVENTS) -> list[dict]:
        """
        GET a calendarView and follow @odata.nextLink pages (Graph returns at most one page
        per call). Stops at max_events so a very large calendar can't stall the page.
        """
        events: list[dict] = []
        next_url, next_params = url, {**params, "$top": _CALENDAR_PAGE_SIZE}
        while next_url and len(events) < max_events:
            resp = requests.get(next_url, headers=headers, params=next_params, timeout=_TIMEOUT)
            resp.raise_for_status()
            body = resp.json()
            events.extend(body.get("value", []))
            next_url, next_params = body.get("@odata.nextLink"), None  # nextLink carries params
        if next_url:
            logger.warning("calendarView truncated at %d events", len(events))
        return events[:max_events]

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
        }
        events = self._calendar_view_all(f"{self.base_url}/me/calendarView", params, headers)
        return [e for e in events if e.get("isOnlineMeeting")]

    def get_event(self, event_id):
        """Fetch a single calendar event directly by its ID."""
        resp = requests.get(
            f"{self.base_url}/me/events/{event_id}",
            headers=self.headers,
            params={
                "$select": "id,subject,start,end,attendees,onlineMeeting,organizer,isOnlineMeeting"
            },
            timeout=_TIMEOUT,
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
            timeout=_TIMEOUT,
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
            timeout=_TIMEOUT,
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
        resp = requests.get(url, headers=self.headers, timeout=_TIMEOUT)
        if resp.status_code != 200:
            logger.warning("list_transcripts: %d - %s", resp.status_code, resp.text[:200])
            return []
        return resp.json().get("value", [])

    def list_meeting_recordings(self, meeting_id: str, user_id: str) -> list[dict] | None:
        """
        Recordings attached to an online meeting, regardless of who clicked Record or
        where Teams stored the file. Needs OnlineMeetingRecording.Read.All (application)
        and the Teams application access policy.
        Returns None when the API call itself fails (e.g. 403 before the permission is
        granted), so callers can tell "no recordings" apart from "couldn't check".
        """
        try:
            resp = requests.get(
                f"{self.base_url}/users/{quote(user_id, safe='')}/onlineMeetings/"
                f"{quote(meeting_id, safe='')}/recordings",
                headers=self.headers,
                timeout=_TIMEOUT,
            )
        except requests.RequestException as e:
            logger.warning("list_meeting_recordings failed: %s", e)
            return None
        if resp.status_code != 200:
            logger.warning("list_meeting_recordings: %d - %s", resp.status_code, resp.text[:200])
            return None
        return resp.json().get("value", [])

    def open_recording_stream(
        self, user_id: str, meeting_id: str, recording_id: str, range_header: str | None = None
    ) -> requests.Response | None:
        """Open a streaming download of a meeting recording; forwards Range for seeking."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        if range_header:
            headers["Range"] = range_header
        try:
            resp = requests.get(
                f"{self.base_url}/users/{quote(user_id, safe='')}/onlineMeetings/"
                f"{quote(meeting_id, safe='')}/recordings/{quote(recording_id, safe='')}/content",
                headers=headers,
                stream=True,
                # Longer read timeout: video chunks can be slow to start
                timeout=(_TIMEOUT[0], 60),
            )
        except requests.RequestException as e:
            logger.warning("open_recording_stream failed: %s", e)
            return None
        if resp.status_code not in (200, 206):
            logger.warning("open_recording_stream: HTTP %d", resp.status_code)
            resp.close()
            return None
        return resp

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
        resp = requests.get(
            url, headers=headers, params={"$format": "text/vtt"}, timeout=_TIMEOUT
        )
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
            timeout=_TIMEOUT,
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
        resp = requests.post(url, headers=self.headers, json=payload, timeout=_TIMEOUT)
        if resp.status_code >= 400:
            logger.error(
                "send_mail_as_user failed: %s %s",
                resp.status_code,
                resp.text[:500],
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
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def list_user_calendar_events(self, user_id, start_date, end_date, online_only=True):
        """
        Fetch calendar events for a specific user (requires app-level token).
        online_only=False returns every event, with location + bodyPreview so Zoom
        invites (which aren't Teams online meetings) can be recognised.
        """
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Prefer": 'outlook.timezone="UTC"',
        }
        select = "id,subject,start,end,attendees,onlineMeeting,organizer,isOnlineMeeting,isCancelled"
        if not online_only:
            select += ",location,bodyPreview,onlineMeetingUrl"
        params = {
            "startDateTime": f"{start_date}T00:00:00Z",
            "endDateTime": f"{end_date}T23:59:59Z",
            "$select": select,
            "$orderby": "start/dateTime desc",
        }
        events = self._calendar_view_all(
            f"{self.base_url}/users/{user_id}/calendarView", params, headers
        )
        return [e for e in events if e.get("isOnlineMeeting")] if online_only else events

    def _search_user_recordings(self, user_id, deadline: float | None = None):
        """
        Search a user's OneDrive for Teams recording files with retry on 429.
        Teams saves recordings as: Subject-YYYYMMDD_HHMMSS-Meeting Recording.mp4
        Special chars like | are stripped from filenames by the OS.
        If deadline (time.monotonic() value) is set, gives up instead of waiting past it.
        Successful results are cached per user for a few minutes.
        """
        cached = _RECORDING_SEARCH_CACHE.get(user_id)
        if cached is not None:
            return cached
        for attempt in range(3):
            try:
                resp = requests.get(
                    f"{self.base_url}/users/{user_id}/drive/root/search(q='Meeting Recording')",
                    headers=self.headers,
                    params={"$select": "id,name,createdDateTime", "$top": 200},
                    timeout=_TIMEOUT,
                )
                if resp.status_code == 429:
                    wait = _parse_retry_after(resp)
                    if deadline is not None and time.monotonic() + wait > deadline:
                        logger.warning(
                            "Rate limited recording search for %s, giving up (time budget)", user_id
                        )
                        return []
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
                            "item_id": f.get("id", ""),
                            "user_id": user_id,
                        })
                _RECORDING_SEARCH_CACHE.set(user_id, recordings)
                return recordings
            except Exception as e:
                logger.warning("OneDrive recording search error for %s: %s", user_id, e)
                return []
        return []

    def _resolve_user_id_safe(self, email, deadline: float | None = None):
        """Resolve email to user ID, returning None on failure. Cached per client.
        If deadline (time.monotonic() value) is set, skip a 429 retry that would overrun it."""
        key = (email or "").lower()
        if key in self._uid_cache:
            return self._uid_cache[key]
        uid = self._resolve_user_id_uncached(email, deadline)
        # A miss under a time budget may only be throttling — don't remember it
        if uid is not None or deadline is None:
            self._uid_cache[key] = uid
        return uid

    def _resolve_user_id_uncached(self, email, deadline: float | None = None):
        try:
            resp = requests.get(
                f"{self.base_url}/users/{email}",
                headers=self.headers,
                params={"$select": "id"},
                timeout=_TIMEOUT,
            )
            if resp.status_code == 429:
                wait = _parse_retry_after(resp)
                if deadline is not None and time.monotonic() + wait > deadline:
                    logger.info("Rate limited resolving %s, skipping (time budget)", email)
                    return None
                time.sleep(wait)
                resp = requests.get(
                    f"{self.base_url}/users/{email}",
                    headers=self.headers,
                    params={"$select": "id"},
                    timeout=_TIMEOUT,
                )
            if resp.status_code == 200:
                return resp.json().get("id")
        except Exception as e:
            logger.warning("_resolve_user_id_safe failed for %s: %s", email, e)
        return None

    def build_recording_lookup(self, meetings, user_id, org_domain="",
                               budget_seconds: float | None = None):
        """
        Search OneDrives of the viewed user and all distinct organizers
        for recording files. Only searches organizers (not all attendees)
        to keep response times fast. budget_seconds bounds throttling waits.
        """
        if not org_domain:
            org_domain = getattr(Config, "ORG_DOMAIN", "")
        deadline = time.monotonic() + budget_seconds if budget_seconds else None

        emails_seen = set()
        user_ids_to_search = set()
        user_ids_to_search.add(user_id)

        for m in meetings:
            # Real organizer from the join link (covers channel meetings)
            oid = self.organizer_id_from_join_url(
                (m.get("onlineMeeting") or {}).get("joinUrl", "")
            )
            if oid:
                user_ids_to_search.add(oid)
            org_email = (
                m.get("organizer", {}).get("emailAddress", {}).get("address", "")
            ).lower()
            if org_email and org_email.endswith(f"@{org_domain}") and org_email not in emails_seen:
                emails_seen.add(org_email)
                uid = self._resolve_user_id_safe(org_email, deadline)
                if uid:
                    user_ids_to_search.add(uid)

        logger.info("Recording lookup: searching %d user(s)", len(user_ids_to_search))

        workers = min(len(user_ids_to_search), _LOOKUP_MAX_WORKERS)
        with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
            futures = {pool.submit(self._search_user_recordings, uid, deadline): uid
                       for uid in user_ids_to_search}
            all_recordings = []
            for future in as_completed(futures):
                all_recordings.extend(future.result())

        logger.info("Recording lookup: found %d recording(s)", len(all_recordings))
        return all_recordings

    def build_file_lookup(
        self,
        meetings,
        user_id: str,
        org_domain: str = "",
        include_recordings: bool = False,
    ) -> dict:
        """
        Search OneDrives of the viewed user and all distinct internal organizers for
        transcript files (and recording files only if include_recordings=True).
        Returns {"recordings": [...], "transcripts": [...]}.
        Transcript entries have the same shape as recording entries (subject, date, time)
        plus item_id and ext — so _match_recording() works on both lists.

        Bounded by _LOOKUP_BUDGET_SECONDS: when Graph throttles (429) beyond the budget,
        the affected searches return empty rather than blocking the page.
        """
        if not org_domain:
            org_domain = getattr(Config, "ORG_DOMAIN", "")

        deadline = time.monotonic() + _LOOKUP_BUDGET_SECONDS

        organizer_emails: set = set()
        for m in meetings:
            org_email = (
                m.get("organizer", {}).get("emailAddress", {}).get("address", "")
            ).lower()
            if org_email and org_email.endswith(f"@{org_domain}"):
                organizer_emails.add(org_email)

        user_ids_to_search: set = {user_id}
        if organizer_emails:
            workers = min(len(organizer_emails), _LOOKUP_MAX_WORKERS)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(self._resolve_user_id_safe, email, deadline)
                    for email in organizer_emails
                ]
                for future in as_completed(futures):
                    uid = future.result()
                    if uid:
                        user_ids_to_search.add(uid)

        logger.info("File lookup: searching %d user(s)", len(user_ids_to_search))

        tasks: list[tuple[str, str]] = []
        for uid in user_ids_to_search:
            tasks.append((uid, "transcripts"))
            if include_recordings:
                tasks.append((uid, "recordings"))

        results: dict = {"recordings": [], "transcripts": []}
        workers = min(len(tasks), _LOOKUP_MAX_WORKERS)
        with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
            future_map = {}
            for uid, ftype in tasks:
                if ftype == "recordings":
                    future = pool.submit(self._search_user_recordings, uid)
                else:
                    future = pool.submit(self._search_user_transcripts, uid, deadline)
                future_map[future] = ftype
            for future in as_completed(future_map):
                results[future_map[future]].extend(future.result())

        logger.info(
            "File lookup: %d recording(s), %d transcript file(s)",
            len(results["recordings"]), len(results["transcripts"]),
        )
        return results

    def _search_user_transcripts(
        self, user_id: str, deadline: float | None = None
    ) -> list[dict]:
        """
        Search a user's OneDrive for Teams transcript files with retry on 429.
        Teams saves transcripts as: Subject-YYYYMMDD_HHMMSS-Transcript.vtt (or .docx)
        Returns list of dicts: {subject, date, time, item_id, ext, user_id}.
        If deadline (time.monotonic() value) is set, gives up instead of waiting past it.
        """
        for attempt in range(3):
            try:
                resp = requests.get(
                    f"{self.base_url}/users/{user_id}/drive/root/search(q='Transcript')",
                    headers=self.headers,
                    params={"$select": "id,name,createdDateTime", "$top": 200},
                    timeout=_TIMEOUT,
                )
                if resp.status_code == 429:
                    wait = _parse_retry_after(resp)
                    if deadline is not None and time.monotonic() + wait > deadline:
                        logger.warning(
                            "Rate limited transcript search for %s, giving up (time budget)",
                            user_id,
                        )
                        return []
                    logger.info("Rate limited transcript search for %s, retrying in %ds", user_id, wait)
                    time.sleep(wait)
                    continue
                if resp.status_code != 200:
                    logger.warning("_search_user_transcripts: HTTP %d for %s", resp.status_code, user_id)
                    return []

                transcripts = []
                for f in resp.json().get("value", []):
                    name = f.get("name", "")
                    match = re.search(
                        r"^(.+?)-(\d{8})_(\d{6})(?:UTC)?-Transcript\.",
                        name,
                        re.IGNORECASE,
                    )
                    if not match:
                        continue
                    ext = name.rsplit(".", 1)[-1].lower()
                    if ext not in ("vtt", "docx"):
                        continue
                    transcripts.append({
                        "subject": match.group(1).strip(),
                        "date": match.group(2),
                        "time": match.group(3),
                        "item_id": f.get("id", ""),
                        "ext": ext,
                        "user_id": user_id,
                    })
                logger.info("_search_user_transcripts: %d file(s) for %s", len(transcripts), user_id)
                return transcripts
            except Exception as e:
                logger.warning("OneDrive transcript search error for %s: %s", user_id, e)
                return []
        return []

    def list_attendance(self, user_id: str, meeting_id: str) -> list[dict] | None:
        """
        Attendance reports of an online meeting (one per session/occurrence), each with its
        attendanceRecords expanded. Needs OnlineMeetingArtifact.Read.All (application).
        Returns None when the request fails (permission, not found).
        """
        base = f"{self.base_url}/users/{quote(user_id)}/onlineMeetings/{quote(meeting_id)}"
        try:
            resp = requests.get(f"{base}/attendanceReports", headers=self.headers,
                                timeout=_TIMEOUT)
            if resp.status_code != 200:
                logger.info("list_attendance: HTTP %d", resp.status_code)
                return None
            reports = resp.json().get("value", [])
            for report in reports:
                r = requests.get(f"{base}/attendanceReports/{quote(report['id'])}/attendanceRecords",
                                 headers=self.headers, timeout=_TIMEOUT)
                report["records"] = r.json().get("value", []) if r.status_code == 200 else None
            return reports
        except requests.RequestException as e:
            logger.warning("list_attendance failed: %s", e)
            return None

    def get_recording_file(self, user_id: str, item_id: str) -> dict | None:
        """
        Metadata for a recording in a user's OneDrive, including a short-lived
        pre-authenticated download URL the browser can stream directly (supports seeking).
        """
        return self._drive_item_info(f"users/{quote(user_id)}/drive/items/{quote(item_id)}")

    def get_drive_item_file(self, drive_id: str, item_id: str) -> dict | None:
        """Same as get_recording_file for a file in any drive (e.g. a channel's SharePoint)."""
        return self._drive_item_info(f"drives/{quote(drive_id)}/items/{quote(item_id)}")

    def channel_site_drive_id(self, group_email: str) -> str | None:
        """
        Document library of the SharePoint site behind a Teams team, found from the team's
        group email (M365@cloudfuze.com -> /sites/M365). Channel meeting recordings live
        here, not in anyone's OneDrive. Needs Sites.Read.All. Cached per alias.
        """
        alias = (group_email or "").split("@")[0].strip()
        if not alias:
            return None
        if alias.lower() in _CHANNEL_DRIVE_CACHE:
            return _CHANNEL_DRIVE_CACHE.get(alias.lower())
        drive_id = None
        try:
            resp = requests.get(f"{self.base_url}/sites", headers=self.headers,
                                params={"search": alias}, timeout=_TIMEOUT)
            if resp.status_code == 200:
                for site in resp.json().get("value", []):
                    if (site.get("webUrl") or "").rstrip("/").lower().endswith(f"/sites/{alias.lower()}"):
                        d = requests.get(f"{self.base_url}/sites/{site['id']}/drive",
                                         headers=self.headers, params={"$select": "id"},
                                         timeout=_TIMEOUT)
                        if d.status_code == 200:
                            drive_id = d.json().get("id")
                        break
            else:
                logger.info("channel_site_drive_id: HTTP %d for %s", resp.status_code, alias)
        except requests.RequestException as e:
            logger.warning("channel_site_drive_id failed for %s: %s", alias, e)
            return None  # not cached: try again next time
        _CHANNEL_DRIVE_CACHE.set(alias.lower(), drive_id)
        return drive_id

    def search_drive_recordings(self, drive_id: str, subject: str) -> list[dict]:
        """Meeting-recording .mp4 files in a drive whose name contains the subject."""
        query = re.sub(r"[^\w\s-]", " ", subject or "").strip()[:80]
        if not query:
            return []
        try:
            resp = requests.get(
                f"{self.base_url}/drives/{quote(drive_id)}/root/search(q='{quote(query)}')",
                headers=self.headers, params={"$top": 50}, timeout=_TIMEOUT,
            )
        except requests.RequestException as e:
            logger.warning("search_drive_recordings failed: %s", e)
            return []
        if resp.status_code != 200:
            logger.info("search_drive_recordings: HTTP %d", resp.status_code)
            return []
        return [
            f for f in resp.json().get("value", [])
            if (f.get("name") or "").lower().endswith(".mp4") and "recording" in f["name"].lower()
        ]

    def _drive_item_info(self, path: str) -> dict | None:
        try:
            # No $select: Graph omits @microsoft.graph.downloadUrl when $select is used
            resp = requests.get(f"{self.base_url}/{path}", headers=self.headers, timeout=_TIMEOUT)
        except requests.RequestException as e:
            logger.warning("drive item lookup failed: %s", e)
            return None
        if resp.status_code != 200:
            logger.warning("drive item lookup: HTTP %d", resp.status_code)
            return None
        data = resp.json()
        return {
            "name": data.get("name", ""),
            "size": data.get("size", 0),
            "web_url": data.get("webUrl", ""),
            "created": data.get("createdDateTime", ""),
            "download_url": data.get("@microsoft.graph.downloadUrl", ""),
        }

    def get_onedrive_file_content(self, user_id: str, item_id: str) -> bytes | None:
        """
        Download raw bytes of an OneDrive file by item ID using the app token.
        Uses /users/{user_id}/drive/items/{item_id}/content (follows redirects).
        """
        try:
            resp = requests.get(
                f"{self.base_url}/users/{user_id}/drive/items/{item_id}/content",
                headers=self.headers,
                timeout=30,
                allow_redirects=True,
            )
            if resp.status_code == 200:
                return resp.content
            logger.warning(
                "get_onedrive_file_content: HTTP %d for user=%s item=%s",
                resp.status_code, user_id, item_id,
            )
            return None
        except Exception as e:
            logger.error("get_onedrive_file_content failed for %s/%s: %s", user_id, item_id, e)
            return None
