# Security Rules

These are the standing rules `.claude/agents/security-reviewer.md` enforces and gstack's `/cso` should be pointed at for security-sensitive changes. See also `.claude/memory/domain-knowledge.md` for what counts as "sensitive" in this app (customer transcripts, PII in attendee lists).

## Secrets
- All secrets (`AZURE_CLIENT_SECRET`, `OPENAI_API_KEY`, `ZOOM_CLIENT_SECRET`, `GOOGLE_CLIENT_SECRET`, `FLASK_SECRET_KEY`, DB credentials) live only in `.env`. Never hardcode, never log, never echo.
- `.claude/hooks/block-sensitive-writes.sh` blocks commands that `cat`/`echo`/`git add` `.env` — do not work around this hook.
- `FLASK_SECRET_KEY` must not use the `dev-secret-key-change-in-production` default outside local dev.

## OAuth & Sessions
- Three independent OAuth flows exist: Azure AD/MSAL (`auth.py`), Google OAuth2 (`google_auth.py`), Zoom Server-to-Server (`zoom_auth.py`). Do not blend their token storage or scopes.
- Never request scopes beyond `Config.SCOPES` (Graph) or `_GOOGLE_SCOPES` (Google). Adding a scope requires updating `.claude/rules/api-conventions.md` and re-running admin consent.
- Google tokens live in the Flask session dict (`google_access_token`, `google_refresh_token`, `google_token_expiry`). Never persist them to the DB or logs.
- On any OAuth callback (`/auth/callback`, `/google/callback`), validate the `state` parameter before exchanging the code.
- Session cookies: `SESSION_COOKIE_HTTPONLY = True` always; `SESSION_COOKIE_SECURE` must be `true` in any non-localhost deployment.

## Route Protection
- Every route except `/`, `/login`, `/login-page`, `/auth/callback`, `/google/callback` (if added), and `/favicon.ico` must use `@login_required`.
- Admin-only routes (`/admin`, `/admin/*`) must additionally check `current_email.lower() in config.ADMIN_EMAILS`.
- Never trust a `next`/redirect parameter without validating it points inside the app (open redirect risk).

## External API Calls
- Never pass raw, unsanitised user input into an OpenAI prompt, a Graph query, or a Google Calendar query string.
- Uploaded transcript filenames must go through `werkzeug.secure_filename` — never build a filesystem path directly from user input (path traversal).
- Enforce `TRANSCRIPT_UPLOAD_MAX_BYTES` and `ALLOWED_TRANSCRIPT_SUFFIXES` on every upload path, not just the primary one.

## Error Handling
- Never return raw exception text or stack traces to the client. Log the full exception server-side (`logger.exception(...)`), return a generic message in the `jsonify({"status": "error", ...})` response.

## When to escalate
Any change to the items above must be reviewed by `.claude/agents/security-reviewer.md` AND gstack's `/cso` before merge — see `.claude/rules/pr-standard.md`.
