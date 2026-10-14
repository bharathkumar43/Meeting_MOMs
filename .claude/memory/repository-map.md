---
name: repository-map
description: File-by-file reference for the Meeting MOM Generator repository
metadata:
  type: project
---

Use this before grepping the whole repo — it's usually faster to jump straight to the right file.

## Root
- `run.py` — entrypoint, `python run.py`, port 5100.
- `config.py` — `Config` class, loads `.env`; all environment-driven settings (Azure, Zoom, Google, OpenAI, DB, audit report).
- `docker-compose.yml` — `mom-db` (Postgres 16) + `mom-app` services.
- `Dockerfile` — app image build.
- `deploy.ps1` — Windows PowerShell deploy helper script.
- `requirements.txt` — pinned dependencies (note: psycopg2-binary must stay ≥2.9.12, see [[decisions]]).
- `.env.example` — template for required environment variables.
- `README.md` — **has an unresolved git merge conflict as of this writing**, see [[progress]] known issues.

## app/
- `__init__.py` — app factory: session config, SQLAlchemy/Migrate init, blueprint registration.
- `routes.py` — all ~20 routes on `main_bp`. Largest file; see route list below.
- `models.py` — `User`, `MeetingAccess`, `MOMSent` (SQLAlchemy models — the only place models are defined).
- `auth.py` — Azure AD MSAL helpers (Teams/Outlook source): `get_auth_url`, `process_auth_callback`, `get_token`, `get_app_token`, `logout`.
- `graph_client.py` — `GraphClient`, Microsoft Graph API wrapper.
- `zoom_auth.py` — `get_zoom_access_token()`, Zoom Server-to-Server OAuth.
- `zoom_client.py` — `ZoomClient`, Zoom API wrapper (VTT transcript fetch).
- `google_auth.py` — Google OAuth2 (delegated, session-stored tokens): `get_google_auth_url`, `exchange_code_for_tokens`, `get_google_token`, `revoke_google_token`.
- `google_client.py` — `GoogleMeetClient`, wraps Google Calendar v3 + Google Meet v2 (`list_calendar_events`, `find_conference_record`, `list_transcripts`, `build_transcript_text`, `normalize_event`).
- `meeting_filter.py` — `filter_customer_meetings`, `filter_by_subject`, `parse_vtt_transcript`, `transcript_to_readable` — provider-agnostic.
- `mom_generator.py` — `generate_mom_from_transcript()` — the only OpenAI call site.
- `doc_generator.py` — `generate_mom_document()` (.docx), `convert_docx_to_pdf()`.
- `email_sender.py` — `send_mom_email()` — the only mail-send site (Graph `Mail.Send`).
- `activity_tracker.py` — `record_login`, `record_meeting_access`, `record_mom_sent`, `get_all_users`, `get_user_stats`, `get_pending_moms`, `get_sent_moms`, `get_managers`, `get_non_managers`, `get_audit_rows`, `get_audit_totals`, `get_all_user_dashboard_stats`.
- `audit_report_email.py` — `send_daily_audit_report()` — app-token Graph send for the daily rollup.

### app/routes.py — route index
| Route | Purpose |
|---|---|
| `/favicon.ico` | 204 no-op |
| `/`, `/login`, `/login-page` | landing / Microsoft login |
| `/auth/callback` | MSAL OAuth callback |
| `/logout` | clears session |
| `/admin/grant-consent` | admin-consent helper |
| `/dashboard` | Teams meeting list |
| `/transcript` | fetch/display a Teams transcript |
| `/transcript/parse-file` (POST) | parse an uploaded transcript file |
| `/mom-builder` (POST) | generate MOM JSON from transcript text |
| `/send` (POST) | build the MOM document |
| `/download-mom` | download the built document |
| `/send-email` (POST) | email the MOM |
| `/admin` | admin dashboard |
| `/admin/send-audit-report` (POST) | manually trigger the daily audit email |
| `/admin/user-meetings/<user_email>` | per-user activity drill-down |
| `/zoom/dashboard`, `/zoom/transcript` | Zoom equivalents |
| `/google/dashboard`, `/google/transcript` | Google Meet equivalents |

## app/templates/
- `base.html` — shared layout, extended by every page.
- `login.html`, `dashboard.html`, `transcript.html`, `mom_builder.html`, `send.html` — Teams-source flow.
- `zoom_dashboard.html` — Zoom-source dashboard.
- `google_dashboard.html`, `google_transcript_upload.html` — Google Meet-source flow.
- `admin.html`, `admin_user_meetings.html` — admin views.

## app/static/
- `css/` — `bootstrap.min.css`, `bootstrap-icons.min.css`, `style.css` (custom overrides).
- `js/` — `bootstrap.bundle.min.js`, `mom_builder.js`.
- `fonts/` — `bootstrap-icons.woff`, `bootstrap-icons.woff2`.
- `Clip path group.svg`, `cloudfuze_logo.png` — branding assets.

## Not yet present
- `tests/` — no test suite exists yet (see [[progress]]).
