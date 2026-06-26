# /deploy — Deployment Checklist Command

Walks through pre-deploy checks and runs Docker build.

## Usage
```
/deploy
/deploy --check-only
```

## Steps performed
1. Verify `.env` is not staged in git (`git status`).
2. Check `app/static/css/bootstrap.min.css` exists (no CDN fallback needed).
3. Run `docker compose build` and report any errors.
4. If `--check-only`, stop here and report status.
5. Run `docker compose up -d`.
6. Tail logs for 10 seconds: `docker compose logs -f --tail=50`.

## Arguments
- `$ARGUMENTS` — pass `--check-only` to skip the actual deploy and only validate.

## Pre-deploy Checklist
- [ ] All secrets in `.env`, not in code
- [ ] `requirements.txt` up to date
- [ ] DB migrations applied (`flask db upgrade`)
- [ ] Bootstrap assets in `app/static/` (not CDN)
- [ ] No debug mode in production (`FLASK_DEBUG=false`)
