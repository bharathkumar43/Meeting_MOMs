# Meeting MOM Generator

Flask web app for CloudFuze, Inc. that authenticates users via Azure AD (MSAL), fetches meeting transcripts from Microsoft Teams, Zoom, and Google Meet, generates Minutes of Meeting (MOM) documents with OpenAI, and emails them via Microsoft Graph. Deployed via Docker.

---

## Prerequisites — Install gstack once on your machine

This project uses **gstack** for AI-assisted development (code review, QA, security audits, docs, deployment). Every contributor must install gstack **once** on their own machine before using Claude Code on this repo.

**Requirements:** Claude Code, Git, Node.js 18+ ([nodejs.org](https://nodejs.org) LTS). Bun is installed automatically by gstack's setup.

**Windows users:** you must use **Git Bash** (comes with Git for Windows). PowerShell and CMD will NOT work.

### Fastest install — paste this to Claude Code

Open Claude Code (from anywhere on your machine) and paste this exact message:

> Install gstack: run `git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack && cd ~/.claude/skills/gstack && ./setup` then confirm the skills are available by listing `~/.claude/skills/`.

Claude will clone the repo, run setup, and verify. Takes ~60 seconds.

### Manual install

```bash
git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack
cd ~/.claude/skills/gstack
./setup
```

### Verify it works

Reopen this project in Claude Code and type `/office-hours` — if Claude responds with the office-hours flow, gstack is working.

### Update gstack later

Inside any Claude Code session, run `/gstack-upgrade`.

### Troubleshooting

| Problem | Fix |
|---|---|
| `/office-hours` not recognized | `cd ~/.claude/skills/gstack && ./setup` |
| Windows: `bad interpreter: /bin/bash^M` | `cd ~/.claude/skills/gstack && git config core.autocrlf false && git config core.eol lf && git rm --cached -r . && git reset --hard HEAD && ./setup` |
| `/browse` fails | `cd ~/.claude/skills/gstack && bun install && bun run build` |

---

## Tech Stack
- **Backend**: Python 3.14, Flask 3.1, Flask-SQLAlchemy, Flask-Migrate
- **Auth**: MSAL (Azure AD / OAuth2) for Microsoft, raw OAuth2 for Google (`app/google_auth.py`), Zoom Server-to-Server OAuth. Session stored on filesystem via flask-session.
- **Database**: PostgreSQL (psycopg2-binary 2.9.12 — required for the cp314 wheel)
- **AI**: OpenAI API (GPT) for MOM generation
- **Integrations**: Microsoft Graph API (Teams transcripts, calendar, mail), Zoom API, Google Calendar + Google Meet API
- **Docs**: python-docx, docx2pdf, cairosvg, reportlab
- **Frontend**: Bootstrap 5.3 + Bootstrap Icons (served locally from `app/static` — no CDN)
- **Deployment**: Docker + docker-compose, gunicorn

## Architecture Summary
Single Flask app factory (`app/__init__.py`) registering one blueprint (`main_bp` in `app/routes.py`). Three meeting sources (Teams/Graph, Zoom, Google Meet) each have a dedicated auth module and API client module that normalize into a shared meeting dict shape consumed by `transcript.html` and the MOM builder. Transcripts go through `mom_generator.py` (OpenAI) to produce a `{tldr, action_items, discussion_points}` JSON, which `doc_generator.py` renders to `.docx`/`.pdf` and `email_sender.py` sends via Graph `Mail.Send`. All user activity is recorded to Postgres via `activity_tracker.py` for the admin dashboard and daily audit email. See [.claude/memory/architecture.md](.claude/memory/architecture.md) for the full breakdown.

## Critical Constraints
- **Never load Bootstrap from CDN** — served from `app/static/` only.
- **Never commit `.env`, `venv/`, or `migrations/`** — all gitignored.
- **DB models live only in `app/models.py`.**
- **All routes go in `app/routes.py`** under the `main_bp` blueprint.
- `.git` directory lives outside OneDrive at `C:\GitRepos\Meeting_MOMs` (mmap issue) — see [.claude/memory/decisions.md](.claude/memory/decisions.md).
- psycopg2-binary must stay ≥2.9.12 (Python 3.14 cp314 wheel support).

## Repository Navigation
```
app/
  __init__.py          # App factory, DB/session init
  routes.py             # All Flask routes (main_bp) — ~20 routes, largest file
  models.py             # User, MeetingAccess, MOMSent (SQLAlchemy)
  auth.py               # Azure AD MSAL auth helpers (Teams source)
  graph_client.py       # Microsoft Graph API client
  zoom_auth.py           # Zoom Server-to-Server OAuth
  zoom_client.py         # Zoom API client
  google_auth.py         # Google OAuth2 (delegated, session-stored tokens)
  google_client.py       # Google Calendar + Google Meet API client
  mom_generator.py        # OpenAI transcript -> MOM JSON
  doc_generator.py        # MOM JSON -> .docx / .pdf
  email_sender.py         # Send MOM via Graph API Mail.Send
  meeting_filter.py       # Filter meetings, parse VTT transcripts
  activity_tracker.py     # DB read/write helpers for audit
  audit_report_email.py   # Daily audit email
  templates/               # Jinja2 templates (base, dashboard, zoom_dashboard, google_dashboard, ...)
  static/                  # css/, js/, fonts/ — all local, no CDN
config.py                 # Config class (loads .env)
run.py                    # Entrypoint — python run.py (port 5100)
```
Full file-by-file map: [.claude/memory/repository-map.md](.claude/memory/repository-map.md).

## Memory Files
- [project-context.md](.claude/memory/project-context.md) — core facts, why this exists
- [architecture.md](.claude/memory/architecture.md) — module boundaries, request flow
- [decisions.md](.claude/memory/decisions.md) — architectural decisions and why
- [domain-knowledge.md](.claude/memory/domain-knowledge.md) — MOM domain concepts, meeting filtering rules
- [progress.md](.claude/memory/progress.md) — what works, known issues, backlog
- [repository-map.md](.claude/memory/repository-map.md) — file-by-file reference

## Environment Variables (required in .env)
```
AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID
REDIRECT_URI=http://localhost:5100/auth/callback
FLASK_SECRET_KEY
OPENAI_API_KEY
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/meeting_moms
ADMIN_EMAILS=email1@domain.com,email2@domain.com
MANAGER_EMAILS=email1@domain.com,email2@domain.com
ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET
GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
GOOGLE_REDIRECT_URI=http://localhost:5100/google/callback
ORG_DOMAIN=cloudfuze.com
AUDIT_REPORT_ENABLED=false
AUDIT_SENDER_MAILBOX, AUDIT_REPORT_RECIPIENTS
```

## Common Commands
```bash
.\venv\Scripts\Activate.ps1        # Windows — activate venv
python run.py                      # Run locally on http://localhost:5100
docker compose up -d --build       # Run via Docker
flask db migrate -m "description"  # Create migration
flask db upgrade                   # Apply migrations
pytest --cov=app --cov-report=term-missing   # Run tests with coverage
```

---

## Available gstack Commands

gstack is installed globally at `~/.claude/skills/gstack`. Use `/browse` from gstack for all web browsing; never use `mcp__claude-in-chrome__*` tools.

- **Planning:** `/office-hours`, `/autoplan`, `/spec`, `/plan-ceo-review`, `/plan-eng-review`, `/plan-design-review`
- **Review & investigate:** `/review`, `/investigate`, `/codex`
- **Testing:** `/qa <url>`, `/qa-only <url>`, `/browse`, `/open-gstack-browser`
- **Security & docs:** `/cso`, `/document-release`, `/document-generate`
- **Ship & deploy:** `/ship`, `/land-and-deploy`, `/canary`
- **Safety:** `/careful`, `/freeze`, `/guard`, `/unfreeze`
- **Learn & upgrade:** `/learn`, `/gstack-upgrade`

This project's own commands live in `.claude/commands/` and do not collide with the above: `/team-review` (project checklist — gstack owns `/review`), `/deploy` (project-specific Docker/Bootstrap checks — gstack owns `/land-and-deploy` for full orchestration), `/scaffold`, `/feature`, `/bugfix`.

## Recommended Workflow

- New feature: `/office-hours` → `/autoplan` → implement → `/review` → `/qa` → `/cso` → `/ship`
- Routine change: implement → `/review` → `/qa` → `/ship`
- Bug fix: `/investigate` → fix → `/review` → `/qa` → `/ship`

Before every PR (never skip):
1. `/review` — bugs CI won't catch
2. `/qa <staging-url>` — real browser test
3. `/cso` — security audit (if security-sensitive: auth, tokens, OAuth scopes, file uploads)
4. `/ship` — opens PR

## Pre-flight — gstack availability check

Before offering the Skill routing menu OR running any gstack slash command, Claude MUST first verify gstack is installed:

```bash
test -f ~/.claude/skills/gstack/setup && echo "gstack_installed" || echo "gstack_missing"
```

- `gstack_installed` → show Menu A below
- `gstack_missing` → show Menu B below

Install command (used when the user chooses "Install gstack now"):
```bash
git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack && cd ~/.claude/skills/gstack && ./setup
```

After install, tell the user: "✅ gstack installed. Reopen this project in Claude Code so the new skills are discovered. Then re-ask your original question."

If install fails, report the error, suggest the manual install, and fall back to the normal project approach.

## Skill routing

Before any repository task, Claude must run the Pre-flight check and show the correct menu.

**Menu A — gstack IS installed**

"Before I start, choose one:
1. Use gstack workflow
2. Use normal project files / plain Claude approach
3. Let Claude recommend the best option first"

**Menu B — gstack is NOT installed**

"gstack is not installed on your machine. Before I start, choose one:
1. Install gstack now (~60 seconds), then use gstack workflow
2. Use normal project files / plain Claude approach (no gstack workflows available)
3. Let Claude recommend the best option first"

The install option MUST appear on every question until gstack is installed — not just the first time.

**Slash command exception:** if the user types a gstack slash command (`/review`, `/qa`, `/cso`, `/ship`, `/office-hours`, etc.) directly, run the Pre-flight check first. If installed, run the command directly. If not, show Menu B.

Claude must wait for the user's selection before reading files, editing files, or invoking any skill.

**Option 1 (Menu A) — Use gstack workflow**

Mappings:
- Product brainstorm / feature ideas → `/office-hours`
- Rough idea to spec → `/spec`
- Scope tradeoffs → `/plan-ceo-review`
- New-feature architecture → `/plan-eng-review`
- Bugs / unexpected errors → `/investigate`
- Test a URL → `/qa` or `/qa-only`
- Diff review before land → `/review`
- Security-sensitive change → `/cso`
- Open a PR → `/ship`
- Deploy / verify prod → `/land-and-deploy`
- Docs update → `/document-release`
- Docs generation → `/document-generate`

**Option 1 (Menu B) — Install gstack now**

Run the install command. On success, tell the user to reopen the project. On failure, fall back to Option 2.

**Option 2 — Use normal project files / plain Claude approach**

Reading files, explaining code, small edits, typo fixes, one-file updates, basic refactoring, config changes, project Q&A, checking implementation details.

**Option 3 — Let Claude recommend**

If gstack installed → recommend between gstack workflow and normal approach. If gstack missing → recommend between installing gstack (for tasks that need it) or normal approach (for small tasks).
