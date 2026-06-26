# Skill: PR Description

## Description
Triggered when the user asks to write a PR description, create a pull request, or summarize changes for review. Reads `.claude/rules/pr.md` for format.

## What this skill does
1. Runs `git log origin/main..HEAD --oneline` to list commits in the PR.
2. Runs `git diff origin/main...HEAD --stat` for changed files.
3. Reads the changed files to understand what actually changed.
4. Drafts a PR title (≤70 chars) and description using the project template from `pr.md`.
5. Fills in the test checklist based on what was changed.

## Output
Produces a ready-to-paste PR description following the template in `.claude/rules/pr.md`.

## Project-specific checklist items always included
- [ ] No CDN links introduced
- [ ] `.env` not committed
- [ ] Bootstrap served from `app/static/`
- [ ] No secrets in code
- [ ] `requirements.txt` updated if new packages added
