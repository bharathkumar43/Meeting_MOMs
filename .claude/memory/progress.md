---
name: progress
description: Current development state, what works, what is in progress
metadata:
  type: project
---

## Working (as of 2026-06-27)
- Azure AD login / logout via MSAL
- Teams meeting transcript fetch via Graph API
- MOM generation via OpenAI (tldr, action_items, discussion_points)
- .docx and .pdf export
- Email via Graph API Mail.Send
- Zoom meeting transcript fetch and MOM generation
- Admin dashboard with user activity tracking
- Daily audit email report
- Bootstrap 5.3 + Icons served locally (no CDN)
- Docker deployment via docker-compose
- PostgreSQL database with Flask-Migrate

## Environment
- Dev: Windows 11, Python 3.14.6, venv at `.\venv\`
- Prod: Linux (Docker), gunicorn, PostgreSQL

## Known Issues / Tech Debt
- No automated tests exist yet (tests/ directory not created)
- `migrations/` is gitignored — must be regenerated on fresh clone
- No rate limiting on MOM generation endpoint (OpenAI cost risk)
- Zoom transcript polling has no retry on transient 429 errors

## Next Steps (backlog)
- Add pytest test suite (use test-writer agent)
- Add rate limiting to `/generate-mom` route
- Add Zoom 429 retry with exponential backoff
- Consider splitting routes.py as app grows
