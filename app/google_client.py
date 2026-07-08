"""Google Meet + Calendar REST API client."""

import logging
from typing import Any

import requests

from config import Config

logger = logging.getLogger(__name__)

_CAL_BASE = "https://www.googleapis.com/calendar/v3"
_MEET_BASE = "https://meet.googleapis.com/v2"
_MAX_CAL_PAGES = 3
_MAX_ENTRY_PAGES = 5
_MEET_CONF_TYPES = {"hangoutsMeet", "hangouts"}


class GoogleMeetClient:
    """Wrapper around Google Calendar API v3 and Google Meet REST API v2."""

    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    # ── Calendar ──────────────────────────────────────────────────────────────

    def get_calendar_event(self, event_id: str) -> dict | None:
        """
        Fetch a single Google Calendar event by ID.
        GET /calendars/primary/events/{event_id}
        Returns the raw event dict or None on error.
        """
        try:
            resp = requests.get(
                f"{_CAL_BASE}/calendars/primary/events/{event_id}",
                headers=self.headers,
                timeout=10,
            )
            if resp.status_code in (401, 403, 404):
                logger.warning("get_calendar_event %s: HTTP %d", event_id, resp.status_code)
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("get_calendar_event failed for %s: %s", event_id, e)
            return None

    def list_calendar_events(
        self, from_date: str, to_date: str, keyword: str = ""
    ) -> list[dict]:
        """
        List Google Calendar events with a Google Meet conference link.
        from_date / to_date are 'YYYY-MM-DD' strings.
        Returns normalized meeting dicts (see normalize_event).
        """
        params: dict[str, Any] = {
            "timeMin": f"{from_date}T00:00:00Z",
            "timeMax": f"{to_date}T23:59:59Z",
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 250,
            "fields": "items(id,summary,start,end,organizer,attendees,conferenceData),nextPageToken",
        }
        if keyword:
            params["q"] = keyword

        results: list[dict] = []
        page = 0
        next_page_token: str | None = None

        while page < _MAX_CAL_PAGES:
            if next_page_token:
                params["pageToken"] = next_page_token
            try:
                resp = requests.get(
                    f"{_CAL_BASE}/calendars/primary/events",
                    headers=self.headers,
                    params=params,
                    timeout=15,
                )
                if resp.status_code == 401:
                    logger.warning("list_calendar_events: 401 — token may be expired")
                    break
                resp.raise_for_status()
                data = resp.json()
                for event in data.get("items", []):
                    conf = event.get("conferenceData", {})
                    sol_type = (
                        conf.get("conferenceSolution", {}).get("key", {}).get("type", "")
                    )
                    if sol_type in _MEET_CONF_TYPES:
                        meeting_code = conf.get("conferenceId", "")
                        results.append(self.normalize_event(event, meeting_code))
                next_page_token = data.get("nextPageToken")
                if not next_page_token:
                    break
            except Exception as e:
                logger.warning("list_calendar_events page %d failed: %s", page, e)
                break
            page += 1

        logger.info("list_calendar_events: %d Google Meet events found", len(results))
        return results

    # ── Conference Records ────────────────────────────────────────────────────

    def find_conference_record(self, meeting_code: str) -> dict | None:
        """
        Find the most recent conferenceRecord for a given meeting code.
        GET /v2/conferenceRecords?filter=space.meetingCode="{meeting_code}"
        Returns the first result dict or None.
        """
        if not meeting_code:
            return None
        try:
            resp = requests.get(
                f"{_MEET_BASE}/conferenceRecords",
                headers=self.headers,
                params={"filter": f'space.meetingCode="{meeting_code}"', "pageSize": 5},
                timeout=15,
            )
            if resp.status_code in (400, 401, 403, 404):
                logger.info(
                    "find_conference_record %s: HTTP %d", meeting_code, resp.status_code
                )
                return None
            resp.raise_for_status()
            records = resp.json().get("conferenceRecords", [])
            return records[0] if records else None
        except Exception as e:
            logger.warning("find_conference_record failed for %s: %s", meeting_code, e)
            return None

    # ── Transcripts ───────────────────────────────────────────────────────────

    def list_transcripts(self, conference_record_name: str) -> list[dict]:
        """
        List transcripts for a conference record.
        GET /v2/{conferenceRecord}/transcripts
        Returns list of transcript dicts (empty if none or on error).
        """
        try:
            resp = requests.get(
                f"{_MEET_BASE}/{conference_record_name}/transcripts",
                headers=self.headers,
                params={"pageSize": 10},
                timeout=15,
            )
            if resp.status_code in (401, 403, 404):
                return []
            resp.raise_for_status()
            return resp.json().get("transcripts", [])
        except Exception as e:
            logger.warning(
                "list_transcripts failed for %s: %s", conference_record_name, e
            )
            return []

    def get_transcript_entries(self, transcript_name: str) -> list[dict]:
        """
        Fetch all transcript entries (paginated).
        GET /v2/{transcript}/entries?pageSize=100
        Returns flat list of entry dicts: {participant, text, startTime, endTime}.
        """
        entries: list[dict] = []
        params: dict[str, Any] = {"pageSize": 100}
        page = 0
        while page < _MAX_ENTRY_PAGES:
            try:
                resp = requests.get(
                    f"{_MEET_BASE}/{transcript_name}/entries",
                    headers=self.headers,
                    params=params,
                    timeout=20,
                )
                if resp.status_code in (401, 403, 404):
                    break
                resp.raise_for_status()
                data = resp.json()
                entries.extend(data.get("transcriptEntries", []))
                next_token = data.get("nextPageToken", "")
                if not next_token:
                    break
                params["pageToken"] = next_token
            except Exception as e:
                logger.warning("get_transcript_entries page %d failed: %s", page, e)
                break
            page += 1
        return entries

    # ── Participants ──────────────────────────────────────────────────────────

    def get_participant_name(self, participant_resource_name: str) -> str:
        """
        Resolve a participant resource path to a display name.
        GET /v2/{participant}
        Returns displayName, or email, or 'Unknown' as fallback.
        """
        try:
            resp = requests.get(
                f"{_MEET_BASE}/{participant_resource_name}",
                headers=self.headers,
                timeout=10,
            )
            if not resp.ok:
                return "Unknown"
            data = resp.json()
            user = data.get("signedinUser", {})
            return (
                user.get("displayName")
                or user.get("user", "").split("/")[-1]
                or "Unknown"
            )
        except Exception as e:
            logger.debug(
                "get_participant_name failed for %s: %s", participant_resource_name, e
            )
            return "Unknown"

    def build_transcript_text(self, transcript_name: str) -> str:
        """
        Fetch all entries for a transcript, resolve participant display names,
        and return a readable 'Speaker: text' formatted string.
        Consecutive entries from the same speaker are merged.
        """
        entries = self.get_transcript_entries(transcript_name)
        if not entries:
            return ""

        participant_cache: dict[str, str] = {}
        lines: list[str] = []
        current_speaker = ""
        current_chunks: list[str] = []

        for entry in entries:
            participant_path = entry.get("participant", "")
            text = entry.get("text", "").strip()
            if not text:
                continue

            if participant_path and participant_path not in participant_cache:
                participant_cache[participant_path] = self.get_participant_name(
                    participant_path
                )
            speaker = participant_cache.get(participant_path, "Unknown")

            if speaker != current_speaker:
                if current_chunks:
                    lines.append(f"{current_speaker}: {' '.join(current_chunks)}")
                current_speaker = speaker
                current_chunks = [text]
            else:
                current_chunks.append(text)

        if current_chunks:
            lines.append(f"{current_speaker}: {' '.join(current_chunks)}")

        return "\n".join(lines)

    # ── Normalization ─────────────────────────────────────────────────────────

    @staticmethod
    def normalize_event(event: dict, meeting_code: str) -> dict:
        """
        Convert a Google Calendar event dict to the standard meeting dict
        used by transcript.html and all shared templates.
        """
        subject = event.get("summary", "Google Meet")

        start_raw = (
            event.get("start", {}).get("dateTime")
            or event.get("start", {}).get("date", "")
        )
        end_raw = (
            event.get("end", {}).get("dateTime")
            or event.get("end", {}).get("date", "")
        )

        # Strip to 19 chars (YYYY-MM-DDTHH:MM:SS), handle all-day dates
        def _clean_dt(raw: str) -> str:
            if not raw:
                return ""
            if "T" in raw:
                return raw[:19]
            return f"{raw}T00:00:00"

        start_clean = _clean_dt(start_raw)
        end_clean = _clean_dt(end_raw)

        organizer = event.get("organizer", {})
        organizer_email = organizer.get("email", "")
        organizer_name = organizer.get("displayName", organizer_email)

        attendees = []
        for a in event.get("attendees", []):
            email = a.get("email", "")
            name = a.get("displayName", "")
            if email:
                attendees.append({"emailAddress": {"name": name, "address": email}})

        org_domain = (Config.ORG_DOMAIN or "").lower()
        external_attendees = [
            a
            for a in attendees
            if org_domain
            and a["emailAddress"]["address"].lower().split("@")[-1] != org_domain
        ]

        return {
            "id": f"google_{event.get('id', '')}",
            "subject": subject,
            "start": {"dateTime": start_clean},
            "end": {"dateTime": end_clean},
            "organizer": {
                "emailAddress": {"name": organizer_name, "address": organizer_email}
            },
            "attendees": attendees,
            "external_attendees": external_attendees,
            "has_transcript": False,
            "source": "google",
            "google_event_id": event.get("id", ""),
            "google_meeting_code": meeting_code,
        }
