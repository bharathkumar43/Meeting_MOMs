# Workflow: Bug Fix

Repeatable blueprint for diagnosing and fixing a bug, using gstack for investigation/review/ship and project files for the fix itself.

## Steps

### 1. Investigate (gstack, if root cause unknown)
- `/investigate` — if the root cause isn't obvious from the report alone.

### 2. Reproduce
- Identify the exact route, action, or input that triggers the bug.
- Check Flask logs for the traceback.
- Note the expected vs. actual behavior.

### 3. Locate
- Use Grep to find the relevant code path.
- Read the full function — not just the line that errors.
- Check if the bug is in: route handler, helper module (`meeting_filter.py`, a `*_client.py`, `mom_generator.py`, `doc_generator.py`), template, or DB query.

### 4. Fix
- Make the minimal change that fixes the bug. No unrelated cleanup.
- Do not add error handling for scenarios that can't happen.
- Validate input at the boundary (route handler), not deep in helpers.
- Respect `.claude/rules/architecture-boundaries.md` — don't fix a bug by reaching across a module boundary that isn't supposed to exist.

### 5. Verify
- Run the app and manually reproduce the original scenario.
- Confirm the bug is gone and no regressions introduced.

### 6. Test
- Invoke `.claude/agents/test-writer.md` to add a regression test for this bug.
- The test should fail before the fix and pass after.

### 7. Review
- `/review` — gstack general code review.
- `/team-review` — project convention checklist.
- If the bug was security-related, invoke `.claude/agents/security-reviewer.md` and run gstack's `/cso`.

### 8. QA (gstack)
- `/qa <staging-url>` — confirm the fix in a real browser if the bug was UI-visible.

### 9. Ship
- `/ship` — opens the PR.

### 10. Update memory
- If the bug reveals a recurring pattern, add a rule to `.claude/rules/`.
- Update `.claude/memory/progress.md` known issues section.
