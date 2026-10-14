# /bugfix — Diagnose and Fix a Bug

Runs this project's bug-fix workflow end to end. See `.claude/workflows/bug-fix.md` for the full blueprint this command follows.

## Usage
```
/bugfix <description of the bug or the failing scenario>
```

## What it does
1. Reproduces: identifies the exact route/action/input that triggers the bug and the expected vs. actual behavior.
2. Locates the code path with Grep, reading the full function rather than just the erroring line.
3. Makes the minimal fix — no unrelated cleanup, no speculative error handling.
4. Manually verifies the original scenario is fixed and no regression was introduced.
5. Invokes `.claude/agents/test-writer.md` to add a regression test that fails before the fix and passes after.
6. Runs `/team-review`; invokes `.claude/agents/security-reviewer.md` if the bug was security-related.
7. Updates `.claude/memory/progress.md` (known issues) and adds a rule to `.claude/rules/` if the bug reveals a recurring pattern.

## Arguments
- `$ARGUMENTS` — description of the bug, or steps to reproduce.

## When to use gstack instead
For a bug whose root cause isn't yet known, start with gstack's `/investigate` before running `/bugfix` — see the Skill routing menu in `CLAUDE.md`.
