# AGENTS.md — Subagent Roster

Defines which specialized subagents exist, what each one does, and when to hand off to them. These agents cover **project-specific** concerns gstack's general-purpose commands don't have context for. For general code review, QA, and security audits, use gstack's `/review`, `/qa`, and `/cso` first — see [CLAUDE.md](CLAUDE.md#available-gstack-commands).

## architect
**File**: `.claude/agents/architect.md`
**Trigger**: New meeting source/integration, new DB model, any change that might need a new blueprint, or any refactor spanning more than 2 files.
**Capabilities**: Plans where new code belongs against the existing single-blueprint, client-wrapper architecture; decides whether a feature needs a new `*_auth.py` + `*_client.py` pair.
**Handoff**: Before implementation starts — returns a file-and-module plan, not code.
**Relationship to gstack**: `/plan-eng-review` and `/autoplan` handle general engineering planning; this agent applies that planning to this repo's specific module boundaries.

## code-reviewer
**File**: `.claude/agents/code-reviewer.md`
**Trigger**: After implementation touching `app/routes.py`, any `*_client.py`/`*_auth.py` module, `mom_generator.py`, `doc_generator.py`, or `app/templates/`.
**Capabilities**: Checks project-specific conventions — wrapper-module discipline, transcript decode order, MOM JSON shape, `@login_required` coverage.
**Handoff**: Run after gstack's `/review` (which covers general bugs/style) as a project-specific second pass.
**Relationship to gstack**: `/review` is the general-purpose diff review; this agent covers what `/review` cannot know about this codebase.

## security-reviewer
**File**: `.claude/agents/security-reviewer.md`
**Trigger**: Any change touching auth, session, token handling, Graph/Google/Zoom OAuth scopes, or environment variable access.
**Capabilities**: OWASP Top 10 review, hardcoded-secret detection, scope minimality checks across all three OAuth flows (MSAL, Google, Zoom S2S), unvalidated redirects.
**Handoff**: After code changes to `auth.py`, `zoom_auth.py`, `google_auth.py`, `config.py`, or any route handling tokens or user input passed to external APIs.
**Relationship to gstack**: Always run gstack's `/cso` before shipping a security-sensitive change; this agent goes deeper on this repo's specific OAuth flows and session-token storage pattern.

## test-writer
**File**: `.claude/agents/test-writer.md`
**Trigger**: New route added, new helper function added, bug fixed.
**Capabilities**: Writes pytest tests for Flask routes (test client), unit tests for pure helpers (`meeting_filter.py`, `mom_generator.py`, `doc_generator.py`), mocks Graph/Zoom/Google/OpenAI calls.
**Handoff**: After implementing a feature or fix — pass the file path(s) changed and the expected behavior.

## researcher
**File**: `.claude/agents/researcher.md`
**Trigger**: Questions about Microsoft Graph API, Google Calendar/Meet API, Zoom API capabilities, OpenAI model behavior, or Python package compatibility.
**Capabilities**: Web search + deep read of official docs. Returns a concise findings summary with source URLs.
**Handoff**: When a feature requires an API capability you're unsure exists, or when a package behaves unexpectedly.

## Coordination Rules
- Do not spawn multiple agents that write to the same file simultaneously.
- `architect` runs first for anything structural, before `test-writer` or `code-reviewer` are invoked.
- `code-reviewer` and `security-reviewer` both run AFTER `test-writer` (review the tests too, not just the code).
- `security-reviewer` always runs after `code-reviewer` when both apply.
- `researcher` output feeds into the main session — it does not write code directly.
