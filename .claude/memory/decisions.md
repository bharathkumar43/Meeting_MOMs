---
name: decisions
description: Architectural decisions made during development and why
metadata:
  type: project
---

## Bootstrap served locally (not CDN)
**Decision:** Download Bootstrap CSS/JS/fonts to `app/static/` and reference via `url_for('static', ...)`.
**Why:** CSP in artifact preview iframe and Edge Tracking Prevention both block `cdn.jsdelivr.net`. Serving locally eliminates all CSP violations and works in all environments.
**Files:** `app/static/css/bootstrap.min.css`, `app/static/css/bootstrap-icons.min.css`, `app/static/js/bootstrap.bundle.min.js`, `app/static/fonts/`

## .git directory moved off OneDrive
**Decision:** `.git` folder at `C:\GitRepos\Meeting_MOMs`; project working tree on OneDrive. `.git` file at project root contains `gitdir: C:/GitRepos/Meeting_MOMs`.
**Why:** OneDrive intercepts mmap calls on `.git` files causing `fatal: mmap failed: Invalid argument` on every git operation.
**How to apply:** Never delete the `.git` file in the project root. Always run git commands from the project working tree directory.

## Single blueprint architecture
**Decision:** All routes in `main_bp` blueprint in `app/routes.py`.
**Why:** App is small enough that splitting blueprints adds complexity without benefit. If the app grows significantly, split at domain boundaries (auth_bp, admin_bp, zoom_bp).

## Activity tracking in PostgreSQL (not file logs)
**Decision:** `MeetingAccess`, `MOMSent`, `User` models track all user activity in DB.
**Why:** Enables the admin dashboard and daily audit email without log parsing.

## psycopg2-binary pinned to ≥2.9.12
**Decision:** psycopg2-binary==2.9.12 in requirements.txt.
**Why:** Version 2.9.10 has no cp314 wheel for Python 3.14. 2.9.12 ships the first prebuilt cp314 wheel.

## Google Meet added as a third meeting source (superseded 2026-10-21 — see below)
**Decision:** `app/google_auth.py` (delegated OAuth2, session-stored tokens) + `app/google_client.py` (`GoogleMeetClient`, wrapping Calendar v3 + Meet v2) added alongside the existing Graph and Zoom integrations, following the same auth-module/client-module/normalize-to-shared-dict split.
**Why:** Found already implemented in the codebase (routes `/google/dashboard`, `/google/transcript`, templates `google_dashboard.html`/`google_transcript_upload.html`) but undocumented in `CLAUDE.md`, `api-conventions.md`, and `security-reviewer.md` prior to this scaffold pass — those were updated to close the gap.
**Files:** `app/google_auth.py`, `app/google_client.py`, `app/templates/google_dashboard.html`, `app/templates/google_transcript_upload.html`, `config.py` (`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/`GOOGLE_REDIRECT_URI`).

## Claude Code project structure adopted alongside gstack (2026-08-14)
**Decision:** Full `.claude/` scaffold (rules, commands, skills, agents, memory, workflows) documented per-project, with gstack (`~/.claude/skills/gstack`, installed globally per contributor) handling general-purpose review/QA/security/ship/deploy.
**Why:** Reduce future token usage by letting Claude read `CLAUDE.md` + memory files first instead of scanning the repo, and avoid duplicating what gstack already does well.
**Command rename:** the project's existing `.claude/commands/review.md` collided with gstack's reserved `/review` — renamed to `.claude/commands/team-review.md`. `deploy.md` was kept under its original name since it wraps project-specific Docker/Bootstrap-check logic that doesn't collide with gstack's `/land-and-deploy`.
**Agent rename:** `.claude/agents/research.md` renamed to `.claude/agents/researcher.md` for consistency with the rest of the agent roster naming (no gstack collision — done for clarity, not required).
**Rule rename:** `.claude/rules/pr.md` renamed to `.claude/rules/pr-standard.md` to match the target project structure; referenced from `.claude/skills/pr-description/SKILL.md`.
**How to apply:** Any new project command/skill name must be checked against gstack's reserved list in `CLAUDE.md` before being added.

## Removed Google Meet integration (2026-10-21)
**Decision:** Removed Google Meet integration — decided not to support Google Meet; only Teams and Zoom sources remain.
