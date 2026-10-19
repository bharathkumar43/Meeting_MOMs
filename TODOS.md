# TODOS

## Phase 2: timestamped recording deep links (PR 2)
- **What:** Per-action-item links to the exact moment in the recording. This needs an opt-in timestamped `transcript_to_readable`, an `at` field from `mom_generator` with validation, Teams/Zoom deep-link URL formats, and alignment of the `action_at` form field.
- **Why:** Lets a reader check a MOM line against what was actually said.
- **Pros:** The design is already written; PR 1's `MeetingRecording` table is the foundation.
- **Cons:** Needs OpenAI eval work, deep-link URL-format verification and transcript/video drift measurement (~1-2 days human / ~2 hrs CC).
- **Context:** See the design doc "Timestamped Recording Deep Links in MOMs" (Phase 2) and eng review findings S4/S6. **Gate:** build only if PR 1 gets ≥5 recording-link clicks from ≥3 distinct non-admin users (excluding the builder) within 4 weeks of merge.
- **Depends on / blocked by:** PR 1 merged; the gate result; real "share at current time" URLs captured from one Teams and one Zoom recording.

## Real database migrations (Alembic baseline)
- **What:** Create an Alembic baseline matching today's schema, run `flask db stamp head` on prod, and stop relying on `db.create_all()` (`app/__init__.py:39`) for schema changes.
- **Why:** `create_all` never adds columns to existing tables, so any new column on an existing model breaks production queries.
- **Pros:** Makes future column changes safe; matches the `flask db migrate/upgrade` flow already documented in CLAUDE.md.
- **Cons:** One-time prod stamp step. `migrations/` is gitignored and CLAUDE.md says never to commit it, so that rule and `.claude/memory/decisions.md` need updating (~0.5 day human / ~30 min CC).
- **Context:** `migrations/versions/` is currently empty.
- **Depends on / blocked by:** Nothing. Do it before any PR that alters an existing table.
