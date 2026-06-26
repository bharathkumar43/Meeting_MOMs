# Pull Request Standards

## Branch Naming
- `feature/<short-description>` — new functionality
- `fix/<short-description>` — bug fixes
- `refactor/<short-description>` — code cleanup with no behavior change
- `chore/<short-description>` — deps, config, CI changes

## PR Title
- Under 70 characters. Imperative mood. No trailing period.
- Examples: `Add Zoom transcript download`, `Fix session expiry on Graph 401`, `Upgrade psycopg2-binary to 2.9.12`

## PR Description Template
```
## What changed
- <bullet: what was added/changed/removed>

## Why
<one paragraph: the problem this solves or feature request>

## How to test
- [ ] Step 1
- [ ] Step 2

## Checklist
- [ ] No CDN links introduced
- [ ] .env not committed
- [ ] Bootstrap served from app/static/
- [ ] No secrets in code
- [ ] Tests added/updated
```

## Review Rules
- At least one approval before merge.
- security-reviewer agent must sign off on any auth or API scope changes.
- Never force-push to `main`.
- Squash commits on merge for clean history.
