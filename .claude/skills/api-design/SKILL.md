# Skill: API Design

## Description
Triggered when adding a new Flask route, a new internal JSON endpoint, or a new external API integration (a fourth meeting source, a new OpenAI call site, etc.). Reads `.claude/rules/api-conventions.md`.

**Relationship to gstack**: this is not general API design guidance — gstack has no opinion on this. This skill encodes the specific response shapes and wrapper-module rules already in use in this codebase, so new endpoints and integrations stay consistent with the existing two.

## What this skill does
1. For a new **internal Flask route**: enforces the response conventions below.
2. For a new **external API integration**: enforces the client-wrapper pattern (own `*_client.py`, own `*_auth.py` if a new credential type is involved, own `normalize_*` step into the shared meeting dict).
3. Checks scope minimality — a new integration should request the narrowest OAuth/API scope needed, listed explicitly in `.claude/rules/api-conventions.md`.

## Internal Flask JSON route conventions (from `.claude/rules/api-conventions.md`)
- Success: `jsonify({"status": "ok", "data": ...}), 200`
- Error: `jsonify({"status": "error", "message": "..."}), 4xx` — never the raw exception text.
- Non-JSON routes (page renders) use Flask `flash()` with Bootstrap alert categories (`success`, `danger`, `warning`, `info`) instead.

## External API integration conventions
- One `*_client.py` per provider; never call `requests` directly against a third-party API from `routes.py` or another client module.
- One `*_auth.py` per provider if it has its own credential/token flow (MSAL and Zoom S2S differ — do not share code between them beyond generic helpers).
- MOM generation stays exclusively in `mom_generator.py`; it must return `(result_dict, error_str)` and the result must always be validated against the `{tldr, action_items, discussion_points}` shape before use.
- Any new provider's calendar/meeting data must normalize into the same meeting dict shape used by `GraphClient`/`ZoomClient` so templates don't need per-provider branches.

## When to hand off
For scope/consent questions on a brand-new provider, use `.claude/agents/researcher.md` to confirm the provider's actual API capabilities before designing the integration.
