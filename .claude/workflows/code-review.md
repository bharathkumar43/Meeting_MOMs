# Workflow: Code Review

Repeatable blueprint for reviewing a diff or PR before merge.

## Steps

### 1. Get the diff
```bash
git diff origin/main...HEAD --stat     # what changed
git diff origin/main...HEAD            # full diff
git log origin/main..HEAD --oneline   # commits in this PR
```

### 2. Style & convention check
- Run `/review` — checks against code-style.md and api-conventions.md.
- Fix any CRITICAL or WARNING findings before proceeding.

### 3. Security check
- If auth, tokens, file uploads, or external API calls changed: invoke `@security-reviewer`.
- Do not merge until security-reviewer returns no CRITICAL/HIGH findings.

### 4. Test check
- Confirm tests exist for changed code: `pytest tests/ -v`.
- If missing, invoke `@test-writer`.

### 5. Project rule check
Manually verify:
- [ ] No CDN links in any template
- [ ] `.env` not staged
- [ ] `requirements.txt` updated if new packages added
- [ ] Bootstrap assets still in `app/static/` (not CDN)
- [ ] All new routes have `@login_required` (unless intentionally public)

### 6. PR description
- Run `/pr-description` skill to generate the PR body.
- Copy output to GitHub PR.

### 7. Merge
- Squash and merge on GitHub.
- Delete the feature branch after merge.
- Pull main locally: `git pull origin main`.
