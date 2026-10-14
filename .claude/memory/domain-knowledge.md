---
name: domain-knowledge
description: MOM domain concepts, meeting-classification rules, and business terms specific to this app
metadata:
  type: project
---

**What a "MOM" is here:** a Minutes-of-Meeting document with exactly three sections — `tldr` (short summary bullets), `action_items`, and `discussion_points` — produced by OpenAI from a raw transcript and always validated against this shape before use (see `.claude/rules/api-conventions.md`).

**Why this shape:** it's the minimum structure migration managers need to send a customer-facing summary quickly; anything more free-form was judged to slow down the review-before-send step.

**How to apply:** any change to the MOM JSON shape is a breaking change across `mom_generator.py`, `doc_generator.py`, and every template that renders a MOM (`mom_builder.html`, `send.html`) — update all three together and record the change in [[decisions]].

## Customer-meeting detection
A meeting is treated as a "customer meeting" when it has attendees outside `Config.ORG_DOMAIN` (`cloudfuze.com` by default) — computed as `external_attendees` during normalization in each `*_client.py`. This is distinct from `MEETING_KEYWORDS` (`Customer Call, Migration Review, Client Meeting, Onboarding` by default), which is a secondary subject-line filter used by `filter_customer_meetings()`/`filter_by_subject()` in `meeting_filter.py`.

**Why both filters exist:** external-attendee detection catches meetings regardless of naming; keyword filtering lets a manager narrow an already-large list. Neither alone was sufficient in practice.

## Roles
- **Regular user:** can only see/send MOMs for meetings they attended or organized.
- **Manager** (`Config.MANAGER_EMAILS`): broader visibility — exact scope is enforced in `routes.py`, check there before assuming a manager sees everything.
- **Admin** (`Config.ADMIN_EMAILS`): full access to `/admin` — all users' activity, `/admin/user-meetings/<email>`, and manually triggering the audit report (`/admin/send-audit-report`).

## Recipients on a sent MOM
Every MOM email pre-fills `MOM_DEFAULT_DL_EMAIL` (To) and `MOM_DEFAULT_CC_EMAIL` (Cc) from config, editable by the sender before actually sending — these are organizational distribution-list defaults, not per-meeting values.

## Daily audit report
When `AUDIT_REPORT_ENABLED=true`, `audit_report_email.py` sends a rollup (via `AUDIT_SENDER_MAILBOX`, an app-token Graph send — not a delegated user token) to `AUDIT_REPORT_RECIPIENTS`, covering the trailing `AUDIT_REPORT_DAYS` (default 7) of `MeetingAccess`/`MOMSent` activity. This is the only place in the app that sends mail using `get_app_token()` instead of a per-user delegated token.

## Transcript decode order
Uploaded/fetched transcript bytes are decoded in a fixed fallback order — `utf-8-sig` → `utf-8` → `cp1252` → `latin-1` — because Teams/Zoom exports have been observed in all four encodings depending on the client that generated them. Never reorder or shorten this list without evidence a specific source needs a different order.
