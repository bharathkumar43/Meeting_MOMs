# AGENTS.md — Subagent Roster

Defines which specialized subagents exist, what each one does, and when to hand off to them.

## security-reviewer
**File**: `.claude/agents/security-reviewer.md`
**Trigger**: Any change touching auth, session, token handling, Graph API scopes, or environment variable access.
**Capabilities**: Reviews for OWASP Top 10, checks for hardcoded secrets, verifies scope minimality, flags unvalidated redirects.
**Handoff**: After code changes to `auth.py`, `zoom_auth.py`, `config.py`, or any route that handles tokens or user input passed to external APIs.

## test-writer
**File**: `.claude/agents/test-writer.md`
**Trigger**: New route added, new helper function added, bug fixed.
**Capabilities**: Writes pytest tests for Flask routes (test client), unit tests for pure helpers (`meeting_filter.py`, `mom_generator.py`, `doc_generator.py`).
**Handoff**: After implementing a feature or fix — pass the file path(s) changed and the expected behavior.

## research
**File**: `.claude/agents/research.md`
**Trigger**: Questions about Microsoft Graph API capabilities, Zoom API endpoints, OpenAI model behavior, or Python package compatibility.
**Capabilities**: Web search + deep read of official docs. Returns a concise findings summary with source URLs.
**Handoff**: When a feature requires an API capability you're unsure exists, or when a package behaves unexpectedly.

## Coordination Rules
- Do not spawn multiple agents that write to the same file simultaneously.
- security-reviewer always runs AFTER test-writer (review the tests too, not just the code).
- research agent output feeds into the main session — it does not write code directly.
