# /feature — Start a New Feature

Runs this project's feature-build workflow end to end. See `.claude/workflows/feature-build.md` for the full blueprint this command follows.

## Usage
```
/feature <short description>
```

## What it does
1. If the feature needs an unfamiliar API capability, invokes `.claude/agents/researcher.md`.
2. Invokes `.claude/agents/architect.md` to plan which files/modules to touch, respecting `.claude/rules/architecture-boundaries.md`.
3. Implements the plan (routes in `app/routes.py`, client/auth modules, models, templates).
4. Runs `flask db migrate` / `flask db upgrade` if a model changed.
5. Runs `/team-review` and gstack's `/review`; invokes `.claude/agents/security-reviewer.md` if auth/tokens/user input are involved.
6. Invokes `.claude/agents/test-writer.md` for the changed files, then `pytest tests/`.
7. Updates `.claude/memory/progress.md` and `.claude/memory/decisions.md` if applicable.
8. Hands off to gstack's `/qa <url>`, `/cso` (if security-sensitive), and `/ship`.

## Arguments
- `$ARGUMENTS` — free-text description of the feature to build.

## When to use gstack instead
For a feature big enough to need product framing or scope tradeoffs first, start with gstack's `/office-hours` and `/autoplan` before running `/feature` — see the Skill routing menu in `CLAUDE.md`.
