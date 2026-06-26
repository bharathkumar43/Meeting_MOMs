# Skill: Code Review

## Description
Triggered when the user asks to review code, check a diff, audit a file for issues, or verify correctness before committing. Also triggered by `/review` command.

## What this skill does
1. Reads the target file(s) or `git diff`.
2. Checks against project rules: code-style.md, api-conventions.md, security.
3. Produces a prioritised finding list (CRITICAL / WARNING / SUGGESTION).
4. Suggests concrete fixes inline.

## Rules loaded
- `.claude/rules/code-style.md`
- `.claude/rules/api-conventions.md`

## Focus areas for this project
- No CDN links in templates
- No hardcoded secrets or API keys
- All routes protected by `@login_required` unless intentionally public
- OpenAI/Graph/Zoom calls only through their designated wrapper modules
- Proper error handling on all external API calls
- No bare `except:` blocks
