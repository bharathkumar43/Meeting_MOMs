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

## OpenAI API
- Called only in `app/mom_generator.py` via `generate_mom_from_transcript()`.
- Model selection via environment / Config — do not hardcode model names in route handlers.
- Always validate the JSON response shape before using it. Return `(result_dict, error_str)` tuple.
- Never send raw user input directly to OpenAI without sanitisation.

## Internal API Responses (Flask JSON routes)
- Success: `jsonify({"status": "ok", "data": ...}), 200`
- Error: `jsonify({"status": "error", "message": "..."}), 4xx`
- Never expose raw exception messages to the client in production.
