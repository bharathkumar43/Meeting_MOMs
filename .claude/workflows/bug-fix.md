# Workflow: Bug Fix

Repeatable blueprint for diagnosing and fixing a bug.

## Steps

### 1. Reproduce
- Identify the exact route, action, or input that triggers the bug.
- Check Flask logs for traceback.
- Note the expected vs actual behavior.

### 2. Locate
- Use Grep to find the relevant code path.
- Read the full function — not just the line that errors.
- Check if the bug is in: route handler, helper module, template, or DB query.

### 3. Fix
- Make the minimal change that fixes the bug. No unrelated cleanup.
- Do not add error handling for scenarios that can't happen.
- Validate input at the boundary (route handler), not deep in helpers.

### 4. Verify
- Run the app and manually reproduce the original scenario.
- Confirm the bug is gone and no regressions introduced.

### 5. Test
- Invoke `@test-writer` to add a regression test for this bug.
- The test should fail before the fix and pass after.

### 6. Review
- Run `/review` on the changed file(s).
- If the bug was security-related, invoke `@security-reviewer`.

### 7. Commit
```bash
git add <specific files>
git commit -m "fix: <what was broken and how it was fixed>"
git push origin main
```

### 8. Update memory
- If the bug reveals a recurring pattern, add a rule to `.claude/rules/`.
- Update `.claude/memory/progress.md` known issues section.
