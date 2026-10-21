---
name: architecture
description: Module boundaries and the request-flow narrative for the Meeting MOM Generator
metadata:
  type: project
---

**App factory:** `app/__init__.py` creates the Flask app, configures filesystem sessions (`flask-session`), initializes SQLAlchemy + Flask-Migrate, registers the single blueprint `main_bp`, and calls `db.create_all()` at startup.

**Single blueprint:** every route lives in `app/routes.py` on `main_bp` — see the "Single blueprint architecture" decision in [[decisions]] and the hard rule in `.claude/rules/architecture-boundaries.md`.

**Why:** keeps a small app simple; splitting blueprints was judged unnecessary complexity unless the app grows significantly.

**How to apply:** do not introduce a second blueprint without an explicit architecture discussion (invoke `.claude/agents/architect.md` first).

## The two-meeting-source pattern

Teams (Graph) and Zoom are each implemented as an isolated `*_auth.py` + `*_client.py` pair:

| Source | Auth module | Client module | Client class |
|---|---|---|---|
| Teams/Outlook | `app/auth.py` (MSAL) | `app/graph_client.py` | `GraphClient` |
| Zoom | `app/zoom_auth.py` (S2S OAuth) | `app/zoom_client.py` | `ZoomClient` |

Each client module ends with a `normalize_*`/`normalize_event` step producing the same meeting dict shape: `subject`, `start`/`end` (`dateTime` strings), `organizer`, `attendees`, `external_attendees`, `source`, `has_transcript`. This is what lets `transcript.html`, `mom-builder`, and the send flow treat both sources identically without per-provider branching in the templates.

## Request flow: fetch → build → send

1. User authenticates via one provider (`/login` for Microsoft, Zoom uses app-level S2S so no per-user login).
2. Dashboard route (`/dashboard`, `/zoom/dashboard`) lists candidate meetings, filtered by `meeting_filter.py` (customer-meeting detection via external attendees, keyword matching).
3. `/transcript` (or the provider-specific variant) fetches the raw transcript via the provider's client, decodes text (`_decode_text_file`: `utf-8-sig` → `utf-8` → `cp1252` → `latin-1`), and parses VTT via `parse_vtt_transcript()` where applicable.
4. `/mom-builder` sends the transcript text to `mom_generator.generate_mom_from_transcript()` (OpenAI), which returns `(result_dict, error_str)` where `result_dict` has keys `tldr`, `action_items`, `discussion_points`.
5. `/send` and `/download-mom` call `doc_generator.generate_mom_document()` to build a `.docx` (optionally `convert_docx_to_pdf()` for `.pdf`).
6. `/send-email` calls `email_sender.send_mom_email()`, which uses `GraphClient`'s `Mail.Send` delegated scope — not SMTP, regardless of which provider the meeting came from.
7. `activity_tracker.py` records the access (`MeetingAccess`) and send (`MOMSent`) rows for every step above, powering `/admin` and the daily audit email (`audit_report_email.py`).

## Cross-cutting modules (single-owner — see `.claude/rules/architecture-boundaries.md`)
- `meeting_filter.py` — provider-agnostic; operates only on already-normalized meeting dicts, never imports a `*_client.py`.
- `mom_generator.py` — the only OpenAI call site.
- `doc_generator.py` — the only `.docx`/`.pdf` generation site.
- `email_sender.py` — the only mail-send site (Graph `Mail.Send`).
- `activity_tracker.py` — the only writer for `MeetingAccess`/`MOMSent`/login-tracking rows.

## Data model
Three tables in `app/models.py`: `User` (login tracking), `MeetingAccess` (audit trail of transcript views), `MOMSent` (audit trail of sent MOMs). See [[domain-knowledge]] for what these fields mean in business terms.
