# Testing Standards

## Framework
- Use `pytest` with Flask's test client.
- Test files in `tests/` directory, mirroring `app/` structure.
- Filename pattern: `test_<module>.py`.

## What to Test
- All route handlers: happy path + auth failure (no session) + bad input.
- Pure helpers: `meeting_filter.py`, `mom_generator.py`, `doc_generator.py` — unit test with fixtures.
- DB interactions: use a separate test database or SQLite in-memory (`SQLALCHEMY_DATABASE_URI=sqlite:///:memory:`).

## What NOT to Mock
- Do not mock the database — use a real test DB or SQLite. Mocks mask migration failures.
- Do not mock Flask's session — use the test client's `session_transaction()` instead.

## What to Mock
- External HTTP calls (Graph API, Zoom API, OpenAI) — use `unittest.mock.patch` or `responses` library.
- `send_mom_email()` — always mock in tests to prevent real email sends.

## Fixtures
- `conftest.py` at `tests/` root defines: `app` (test Flask app), `client` (test client), `db_session`.
- Use `pytest.fixture(scope="function")` for DB fixtures to ensure isolation.

## Coverage
- Aim for 80%+ on `app/routes.py` and all helper modules.
- Run: `pytest --cov=app --cov-report=term-missing`
