import logging
from datetime import datetime, timedelta, timezone

import requests

from config import Config

logger = logging.getLogger(__name__)

_MAX_PAGES = 3


class ZoomClient:
    """Wrapper around the Zoom REST API v2 for recordings, transcripts, and participants."""

    def __init__(self, access_token):
        self.access_token = access_token
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        self.base_url = Config.ZOOM_API_BASE

    # ── Recordings ───────────────────────────────────────────

    def list_user_recordings(self, user_email, from_date, to_date):
        """
        List cloud recordings for a user within a date range.
        GET /users/{email}/recordings?from=YYYY-MM-DD&to=YYYY-MM-DD
        Returns a flat list of recording meeting dicts (up to _MAX_PAGES pages).
        """
        url = f"{self.base_url}/users/{user_email}/recordings"
        params = {"from": from_date, "to": to_date, "page_size": 30}
        results = []

        for page in range(_MAX_PAGES):
            try:
                resp = requests.get(url, headers=self.headers, params=params, timeout=15)
                if resp.status_code == 404:
                    logger.info("Zoom user not found: %s", user_email)
                    break
                resp.raise_for_status()
                data = resp.json()
                results.extend(data.get("meetings", []))

                next_token = data.get("next_page_token", "")
                if not next_token:
                    break
                params["next_page_token"] = next_token
            except Exception as e:
                logger.warning("list_user_recordings page %d failed: %s", page, e)
                break

        logger.info("list_user_recordings: found %d recording(s) for %s", len(results), user_email)
        return results

    def get_meeting_recordings(self, meeting_id):
        """
        Fetch recording files for a single past meeting.
        GET /meetings/{meetingId}/recordings
        Returns the recording dict or None on failure.
        """
        try:
            resp = requests.get(
                f"{self.base_url}/meetings/{meeting_id}/recordings",
                headers=self.headers,
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning("get_meeting_recordings %s: %d %s", meeting_id, resp.status_code, resp.text[:200])
                return None
            return resp.json()
        except Exception as e:
            logger.warning("get_meeting_recordings failed for %s: %s", meeting_id, e)
            return None

    # ── Transcripts ──────────────────────────────────────────

    def get_transcript_content(self, download_url):
        """
        Download a VTT transcript file from Zoom.
        Returns the VTT text on success, None on failure.
        """
        try:
            resp = requests.get(
                download_url,
                headers=self.headers,
                allow_redirects=True,
                timeout=30,
            )
            if resp.status_code != 200:
                logger.warning("get_transcript_content: %d %s", resp.status_code, resp.text[:200])
                return None
            return resp.text
        except Exception as e:
            logger.warning("get_transcript_content failed: %s", e)
            return None

    # ── Participants ─────────────────────────────────────────

    def get_meeting_participants(self, meeting_id):
        """
        Fetch participant report for a past meeting.
        GET /report/meetings/{meetingId}/participants
        Returns list of participant dicts. Raises on HTTP error.
        """
        resp = requests.get(
            f"{self.base_url}/report/meetings/{meeting_id}/participants",
            headers=self.headers,
            params={"page_size": 100},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("participants", [])

    # ── Normalization ────────────────────────────────────────

    @staticmethod
    def normalize_recording(recording):
        """
        Convert a Zoom recording dict to a Teams-compatible meeting dict
        that can be used directly with transcript.html and other shared templates.
        """
        if not recording:
            return {}

        topic = recording.get("topic", "Zoom Meeting")
        host_email = recording.get("host_email", "")
        start_time_raw = recording.get("start_time", "")
        duration_mins = int(recording.get("duration") or 0)

        # Parse start / compute end
        start_clean = ""
        end_clean = ""
        if start_time_raw:
            try:
                # Zoom returns ISO 8601 with Z suffix
                start_dt = datetime.fromisoformat(start_time_raw.replace("Z", "+00:00"))
                end_dt = start_dt + timedelta(minutes=duration_mins)
                start_clean = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
                end_clean = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
            except ValueError:
                start_clean = start_time_raw[:19]
                end_clean = start_time_raw[:19]

        # Find the first completed TRANSCRIPT recording file
        transcript_url = ""
        for f in recording.get("recording_files", []):
            if (
                f.get("file_type", "").upper() == "TRANSCRIPT"
                and f.get("status", "").lower() == "completed"
            ):
                transcript_url = f.get("download_url", "")
                break

        meeting_id = str(recording.get("id", ""))
        uuid = recording.get("uuid", meeting_id)

        return {
            "id": f"zoom_{uuid}",
            "subject": topic,
            "start": {"dateTime": start_clean},
            "end": {"dateTime": end_clean},
            "organizer": {
                "emailAddress": {
                    "name": host_email,
                    "address": host_email,
                }
            },
            "attendees": [],
            "external_attendees": [],
            "has_transcript": bool(transcript_url),
            "source": "zoom",
            "zoom_meeting_id": meeting_id,
            "zoom_uuid": uuid,
            "zoom_transcript_url": transcript_url,
        }
