# Meeting MOM Generator — Claude Project Guide

## Project Overview
Flask web app that authenticates users via Azure AD (MSAL), fetches Teams/Outlook meeting transcripts via Microsoft Graph API, generates Minutes of Meeting (MOM) documents using OpenAI, and emails them. Also supports Zoom meeting transcripts. Deployed via Docker.

## Tech Stack
- **Backend**: Python 3.14, Flask 3.1, Flask-SQLAlchemy, Flask-Migrate
- **Auth**: MSAL (Azure AD / OAuth2), session stored on filesystem via flask-session
- **Database**: PostgreSQL (psycopg2-binary 2.9.12)
- **AI**: OpenAI API (GPT) for MOM generation
- **Integrations**: Microsoft Graph API (Teams transcripts, calendar, mail), Zoom API
- **Docs**: python-docx, docx2pdf, cairosvg, reportlab
- **Frontend**: Bootstrap 5.3 + Bootstrap Icons (served locally from app/static — no CDN)
- **Deployment**: Docker + docker-compose, gunicorn

## Project Structure
```
app/
  __init__.py          # App factory, DB init
  routes.py            # All Flask routes (main_bp blueprint)
  models.py            # User, MeetingAccess, MOMSent (SQLAlchemy)
  auth.py              # Azure AD MSAL auth helpers
  graph_client.py      # Microsoft Graph API client
  zoom_auth.py         # Zoom OAuth
  zoom_client.py       # Zoom API client
  mom_generator.py     # OpenAI transcript → MOM JSON
  doc_generator.py     # MOM JSON → .docx / .pdf
  email_sender.py      # Send MOM via Graph API Mail.Send
  meeting_filter.py    # Filter meetings, parse VTT transcripts
  activity_tracker.py  # DB read/write helpers for audit
  audit_report_email.py# Daily audit email
  templates/           # Jinja2 HTML templates (base, dashboard, zoom_dashboard, etc.)
  static/
    css/               # bootstrap.min.css, bootstrap-icons.min.css, style.css
    js/                # bootstrap.bundle.min.js, mom_builder.js
    fonts/             # bootstrap-icons.woff2, bootstrap-icons.woff
config.py              # Config class (loads .env)
run.py                 # Entrypoint — python run.py (port 5100)
```

## Hard Rules
- **Never load Bootstrap from CDN.** All Bootstrap CSS/JS/fonts must be served from `app/static/`.
- **Never commit `.env`** — it is gitignored. Secrets live only in `.env`.
- **Never commit `venv/`** — gitignored. Use `.\venv\Scripts\python.exe` or activate first.
- **Never commit `migrations/`** — gitignored. Run `flask db migrate` locally.
- **DB models live only in `app/models.py`.** Do not scatter model definitions.
- **All routes go in `app/routes.py`** under `main_bp` blueprint. Do not create new blueprints without discussion.
- **Do not add inline `<script>` CDN links** in any template. All JS must be local or inline.

## Key Conventions
- Auth guard: use the `@login_required` decorator (defined in routes.py) on all protected routes.
- Admin guard: check `current_email.lower() in config.ADMIN_EMAILS` for admin-only routes.
- Transcript decode order: `utf-8-sig` → `utf-8` → `cp1252` → `latin-1` (see `_decode_text_file`).
- MOM output is a JSON dict with keys: `tldr`, `action_items`, `discussion_points`.
- Document generation: `generate_mom_document()` → `.docx`; `convert_docx_to_pdf()` → `.pdf`.
- Email uses Graph API (`Mail.Send` delegated scope) not SMTP.
- Zoom uses Server-to-Server OAuth (account_id + client_id + client_secret).

## Environment Variables (required in .env)
```
AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID
REDIRECT_URI=http://localhost:5100/auth/callback
FLASK_SECRET_KEY
OPENAI_API_KEY
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/meeting_moms
ADMIN_EMAILS=email1@domain.com,email2@domain.com
ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET
ORG_DOMAIN=cloudfuze.com
```

## Running Locally
```bash
.\venv\Scripts\Activate.ps1        # Windows
python run.py                      # Starts on http://localhost:5100
```

## Running via Docker
```bash
docker compose up -d --build
```

## Database Migrations
```bash
flask db migrate -m "description"
flask db upgrade
```
