---
name: project-context
description: Core facts about the Meeting MOM Generator project — stack, deployment, key constraints
metadata:
  type: project
---

Meeting MOM Generator is a Flask web app for CloudFuze, Inc. that:
- Authenticates users via Azure AD (MSAL OAuth2)
- Fetches Teams meeting transcripts via Microsoft Graph API
- Fetches Zoom meeting transcripts via Zoom Server-to-Server OAuth
- Generates Minutes of Meeting documents using OpenAI GPT
- Emails MOMs as .docx/.pdf attachments via Graph API Mail.Send

**Why:** Internal tool to automate post-meeting documentation for customer calls, migration reviews, and onboarding sessions.

**How to apply:** All feature decisions should align with this core purpose. New integrations should follow the existing pattern of dedicated client modules (`graph_client.py`, `zoom_client.py`) — see [[architecture]] and `.claude/rules/architecture-boundaries.md`.

Key constraints:
- Bootstrap must be served locally (no CDN) — CSP and Edge tracking prevention blocks CDN
- `.git` folder lives at `C:\GitRepos\Meeting_MOMs` (outside OneDrive) to avoid mmap errors
- Python 3.14.6 on Windows dev machine; deployed on Linux via Docker
- psycopg2-binary must be ≥2.9.12 for Python 3.14 cp314 wheel support
- Two independent OAuth flows (MSAL, Zoom S2S) exist side by side — do not blend their token storage or scopes; see `.claude/rules/security-rules.md`
