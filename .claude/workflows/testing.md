# Workflow: Testing

Repeatable blueprint for building out test coverage — this repo has no `tests/` directory yet (see `.claude/memory/progress.md`), so this workflow doubles as the bootstrap plan.

## Steps

### 1. Bootstrap (first time only)
- Create `tests/conftest.py` with `app`, `client`, and `db_session` fixtures per `.claude/rules/testing-standard.md`.
- Use `SQLALCHEMY_DATABASE_URI=sqlite:///:memory:` for the test app config — never mock the database itself.

### 2. Identify what needs coverage
- Route handlers: happy path + auth failure (no session) + bad input, for every route in `app/routes.py` (see the route index in `.claude/memory/repository-map.md`).
- Pure helpers: `meeting_filter.py`, `mom_generator.py`, `doc_generator.py`, `google_client.py`'s `normalize_event`.
- Prioritize routes/helpers touched by the current change first; backfill the rest opportunistically.

### 3. Write tests
- Invoke `.claude/agents/test-writer.md` or the `testing-patterns` skill, per `.claude/rules/testing-standard.md`.
- Mock all external calls: Graph API, Zoom API, Google Calendar/Meet API, OpenAI, `send_mom_email()`.
- Never mock Flask's session — use `session_transaction()`.

### 4. Run
```bash
pytest --cov=app --cov-report=term-missing
```
Target 80%+ on `app/routes.py` and all helper modules per `.claude/rules/testing-standard.md`.

### 5. Review
- `/team-review` on the new test files — confirm no real external calls slipped through unmocked.

### 6. Wire into CI (if not already)
- If this repo gains a CI pipeline, `pytest --cov=app` should run on every PR before gstack's `/ship` — flag this as a gap if asked about CI, since none currently exists in this repo (no `.github/workflows/` found).
