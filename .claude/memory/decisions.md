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
