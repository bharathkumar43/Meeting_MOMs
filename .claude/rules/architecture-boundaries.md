# Architecture Boundaries

See `.claude/memory/architecture.md` for the full request-flow narrative. This file states the hard boundaries — what must not move or be duplicated.

## Blueprint
- There is exactly one blueprint, `main_bp`, defined in `app/routes.py`. Do not create a second blueprint (e.g. `admin_bp`, `zoom_bp`) without an explicit architecture discussion — see the "Single blueprint architecture" decision in `.claude/memory/decisions.md`.

## Models
- All SQLAlchemy models live in `app/models.py` only (`User`, `MeetingAccess`, `MOMSent`). Never define a model inline in `routes.py` or a helper module.
- Schema changes go through `flask db migrate` / `flask db upgrade` — never hand-edit `migrations/` output, and never run raw DDL against the DB.

## Meeting-source modules (the client-wrapper pattern)
Each meeting source (Teams/Graph and Zoom) follows the same two-module split:
- `*_auth.py` — owns token acquisition/refresh/revocation for that provider only.
- `*_client.py` — owns all HTTP calls to that provider's API, and a `normalize_*`/`normalize_event` step that converts the provider's native event/meeting shape into the shared meeting dict (`subject`, `start`, `end`, `organizer`, `attendees`, `external_attendees`, `source`, `has_transcript`).

Adding a fourth source means adding a new `*_auth.py` + `*_client.py` pair that follows this exact split — not extending an existing provider's client with a second provider's calls, and not calling a new provider's API directly from `routes.py`.

## Cross-cutting modules
- `meeting_filter.py` — provider-agnostic filtering/parsing logic (customer detection, subject matching, VTT parsing). Must not import any `*_client.py` — it operates on already-fetched data.
- `mom_generator.py` — the only module allowed to call OpenAI. Route handlers must go through it, never call the OpenAI SDK directly.
- `doc_generator.py` — the only module allowed to produce `.docx`/`.pdf` output.
- `email_sender.py` — the only module allowed to send mail (via Graph `Mail.Send`). No SMTP anywhere in the codebase.
- `activity_tracker.py` — the only module that writes audit rows (`MeetingAccess`, `MOMSent`, login tracking). Route handlers call into it rather than writing to `db.session` directly for these tables.

## Templates
- Every page template extends `base.html`. Teams and Zoom meetings now share one stored shape (`MeetingRecord` → `record_to_meeting`), so both lists render `teams_meetings.html` with `page_source`; `dashboard.html` is the personal overview. Keep source-specific differences as template parameters rather than forking the list template again.

## When a boundary needs to move
If a task seems to require crossing one of these lines (new blueprint, model outside `models.py`, a `*_client.py` calling another provider), stop and invoke `.claude/agents/architect.md` first, or use gstack's `/plan-eng-review` for a broader design discussion. Do not silently work around a boundary.
