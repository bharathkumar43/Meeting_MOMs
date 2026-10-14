# Workflow: Code Review

Repeatable blueprint for reviewing a diff or PR before merge — gstack's general review plus this project's specific checks.

## Steps

### 1. Get the diff
```bash
git diff origin/main...HEAD --stat     # what changed
git diff origin/main...HEAD            # full diff
git log origin/main..HEAD --oneline    # commits in this PR
```

### 2. General review (gstack)
- `/review` — catches bugs and style issues CI won't.
- Fix any findings before proceeding.

### 3. Project convention check
- `/team-review` — checks against `.claude/rules/code-style.md`, `.claude/rules/api-conventions.md`, `.claude/rules/architecture-boundaries.md`.
- Fix any CRITICAL or WARNING findings before proceeding.

### 4. Security check
- If auth, tokens, OAuth scopes, file uploads, or external API calls changed: invoke `.claude/agents/security-reviewer.md` AND run gstack's `/cso`.
- Do not merge until both return no CRITICAL/HIGH findings — see `.claude/rules/security-rules.md`.

### 5. Test check
- Confirm tests exist for changed code: `pytest tests/ -v`.
- If missing, invoke `.claude/agents/test-writer.md`.

### 6. Project rule check
Manually verify:
- [ ] No CDN links in any template
- [ ] `.env` not staged
- [ ] `requirements.txt` updated if new packages added
- [ ] Bootstrap assets still in `app/static/` (not CDN)
- [ ] All new routes have `@login_required` (unless intentionally public)
- [ ] External API calls go through the correct `*_client.py` wrapper

### 7. QA (gstack)
- `/qa <staging-url>` — real browser test before merge, if the change is UI-visible or touches a live integration.

### 8. PR description
- Use the `pr-description` skill (reads `.claude/rules/pr-standard.md`) to draft the body.
- `/ship` — opens the PR with that description.

### 9. Merge
- Squash and merge on GitHub (or `/land-and-deploy` if this PR should also deploy).
- Delete the feature branch after merge.
- Pull main locally: `git pull origin main`.
