# /review — Code Review Command

Run a code review on the current working diff or a specific file.

## Usage
```
/review
/review app/routes.py
/review --security
```

## What it does
1. Reads the diff (`git diff HEAD`) or the specified file.
2. Checks against `.claude/rules/code-style.md` and `.claude/rules/api-conventions.md`.
3. Flags: security issues, missing error handling, CDN links, hardcoded secrets, missing type hints.
4. Outputs a prioritised list: CRITICAL → WARNING → SUGGESTION.

## Arguments
- `$ARGUMENTS` — optional file path or `--security` flag to focus only on security issues.

## Output Format
```
CRITICAL: <issue> — <file>:<line>
WARNING:  <issue> — <file>:<line>
SUGGEST:  <improvement> — <file>:<line>
```
No issues = "LGTM — no problems found."
