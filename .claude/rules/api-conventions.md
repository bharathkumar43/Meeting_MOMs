# API Conventions

## Microsoft Graph API
- Client wrapper: `app/graph_client.py` — always use `GraphClient`, never call `requests` directly for Graph.
- Token acquisition: delegated token via `get_token(session)` for user-scoped calls; app token via `get_app_token()` for Mail.Send from audit sender.
- Scopes in use: `User.Read`, `Calendars.Read`, `OnlineMeetings.Read`, `OnlineMeetingTranscript.Read.All`, `Mail.Send`.
- Never request broader scopes than those listed in `Config.SCOPES`.
- Base URL constant: `Config.GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"`.
- On 401: re-acquire token, do not cache stale tokens across requests.

## Zoom API
- Client wrapper: `app/zoom_client.py` — always use `ZoomClient`.
- Auth: Server-to-Server OAuth via `get_zoom_access_token()` in `app/zoom_auth.py`.
- Base URL: `Config.ZOOM_API_BASE = "https://api.zoom.us/v2"`.
- Transcripts fetched as VTT format; parse with `parse_vtt_transcript()`.

## Google Calendar / Google Meet API
- Client wrapper: `app/google_client.py` — always use `GoogleMeetClient`, never call `requests` directly for Google Calendar or Meet endpoints.
- Auth: delegated OAuth2 via `app/google_auth.py`. Tokens are stored in the Flask session (`google_access_token`, `google_refresh_token`, `google_token_expiry`) — use `get_google_token(session)` to obtain a valid token; it auto-refreshes within 60s of expiry and clears session state on failure.
- Scopes in use: `openid`, `email`, `calendar.readonly`, `meetings.space.readonly` — defined in `_GOOGLE_SCOPES` in `google_auth.py`. Never request broader scopes.
- Base URLs: `https://www.googleapis.com/calendar/v3` (Calendar), `https://meet.googleapis.com/v2` (Meet).
- Flow to fetch a transcript: `list_calendar_events()` → `find_conference_record(meeting_code)` → `list_transcripts(conference_record_name)` → `build_transcript_text(transcript_name)`.
- All Google events are normalized via `GoogleMeetClient.normalize_event()` into the same meeting dict shape Graph and Zoom events use (`subject`, `start`/`end`, `organizer`, `attendees`, `external_attendees`, `source`), so `transcript.html` and the MOM builder can treat all three sources identically.
- On 401/403/404 from Meet/Calendar endpoints: treat as "not available" (return `None`/`[]`), do not raise — Meet transcripts are frequently missing or not yet processed.

## OpenAI API
- Called only in `app/mom_generator.py` via `generate_mom_from_transcript()`.
- Model selection via environment / Config — do not hardcode model names in route handlers.
- Always validate the JSON response shape before using it. Return `(result_dict, error_str)` tuple.
- Never send raw user input directly to OpenAI without sanitisation.

## Internal API Responses (Flask JSON routes)
- Success: `jsonify({"status": "ok", "data": ...}), 200`
- Error: `jsonify({"status": "error", "message": "..."}), 4xx`
- Never expose raw exception messages to the client in production.
