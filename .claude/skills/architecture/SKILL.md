# Skill: Architecture

## Description
Triggered when the user asks where new code should live, whether a change needs a new module/blueprint, or how a feature fits the existing structure. Also triggered before implementing a new meeting-source integration or DB model.

**Relationship to gstack**: gstack's `/plan-eng-review` runs a general engineering design review panel. This skill is what feeds it project-specific facts — this repo's single-blueprint layout and its client-wrapper pattern — so that a `/plan-eng-review` or `/autoplan` session doesn't have to rediscover them from scratch.

## What this skill does
1. Reads `.claude/memory/architecture.md` and `.claude/rules/architecture-boundaries.md`.
2. Locates the smallest set of existing modules the request can extend.
3. If a new meeting source is being added, applies the `*_auth.py` + `*_client.py` + `normalize_*` pattern established by `graph_client.py`/`zoom_client.py`/`google_client.py`.
4. Flags any request that would cross a boundary in `architecture-boundaries.md` (new blueprint, model outside `models.py`, a client module calling another provider) so it can be raised explicitly rather than silently done.

## This project's structure at a glance
- One Flask app factory (`app/__init__.py`), one blueprint (`main_bp` in `app/routes.py`).
- Three parallel meeting sources (Teams/Graph, Zoom, Google Meet), each with its own auth + client module, converging on one shared meeting dict shape.
- One DB module (`app/models.py`) with three tables: `User`, `MeetingAccess`, `MOMSent`.
- One MOM-generation module (`mom_generator.py`, OpenAI-only), one doc-generation module (`doc_generator.py`), one email module (`email_sender.py`, Graph `Mail.Send`-only).

## When to hand off
For anything bigger than "which existing file does this belong in" — e.g. a genuinely new architectural pattern, splitting the blueprint, or introducing a new persistence layer — hand off to `.claude/agents/architect.md` or gstack's `/plan-eng-review`.
