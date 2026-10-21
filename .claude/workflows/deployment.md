# Workflow: Deployment

This project deploys via Docker Compose (`mom-db` + `mom-app`). Use the project's own `/deploy` command for the local/VPS Docker build-and-run steps, and gstack for orchestration and rollout verification.

## Steps

### 1. Pre-deploy checklist (project-specific)
Run `/deploy --check-only`, which verifies:
- [ ] `.env` is not staged in git
- [ ] `app/static/css/bootstrap.min.css` exists (Bootstrap is local, not CDN)
- [ ] `requirements.txt` is up to date
- [ ] DB migrations are ready (`flask db upgrade` will succeed)
- [ ] `FLASK_DEBUG` is not enabled

### 2. Merge & orchestrate (gstack)
- `/land-and-deploy` — merges and handles deploy orchestration for environments gstack manages.
- `/canary` — if a staged rollout is warranted (e.g. a change touching the shared OAuth/session code, or the daily audit email sender).

### 3. Local / VPS build (project-specific)
If deploying by hand to the CloudFuze VPS or building locally:
```bash
/deploy                       # full checklist + docker compose build + up
docker compose logs -f --tail=50   # tail logs after startup
```
`docker-compose.yml` provisions `mom-db` (Postgres 16, healthchecked) before starting `mom-app`; `DATABASE_URL` is injected pointing at the `mom-db` service, overriding whatever is in `.env`.

### 4. Post-deploy verification
- `/qa <deployed-url>` — real browser test against the deployed instance: login (Microsoft), transcript fetch for at least one of the two sources, MOM generation, send.
- Check `/admin` loads and the daily audit email is scheduled correctly if `AUDIT_REPORT_ENABLED=true`.

### 5. Rollback
- `docker compose down` then redeploy the previous image/tag — there is no automated rollback script in this repo; if one is added, document it here and in `.claude/memory/decisions.md`.

### 6. Record
- Update `.claude/memory/progress.md` if the deploy changes what's "working" vs. "known issues".
