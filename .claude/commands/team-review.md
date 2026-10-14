# /team-review — Project Convention Checklist

> Renamed from `review.md` because gstack reserves `/review` for its own general-purpose diff review. Run gstack's `/review` first for bugs/style; run `/team-review` for this project's specific conventions gstack doesn't know.

Runs the project-specific review checklist on the current working diff or a specific file.

## Usage
```
/team-review
/team-review app/routes.py
/team-review --security
```

## What it does
1. Reads the diff (`git diff HEAD`) or the specified file.
2. Checks against `.claude/rules/code-style.md`, `.claude/rules/api-conventions.md`, `.claude/rules/architecture-boundaries.md`, and (with `--security`) `.claude/rules/security-rules.md`.
3. Flags: client-wrapper-pattern violations, CDN links, hardcoded secrets, missing `@login_required`, transcript decode-order regressions, MOM JSON shape drift, missing type hints.
4. Outputs a prioritised list: CRITICAL → WARNING → SUGGESTION.

## Arguments
- `$ARGUMENTS` — optional file path or `--security` flag to focus only on security issues (delegates to `.claude/agents/security-reviewer.md`).

## Output Format
```
CRITICAL: <issue> — <file>:<line>
WARNING:  <issue> — <file>:<line>
SUGGEST:  <improvement> — <file>:<line>
```
No issues = "LGTM — no problems found."

## Before a PR
This command does not replace gstack. Full pre-PR sequence: gstack `/review` → `/team-review` → gstack `/qa` → gstack `/cso` (if security-sensitive) → gstack `/ship`.
