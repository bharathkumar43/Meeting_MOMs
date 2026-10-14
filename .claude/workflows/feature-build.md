# Workflow: Feature Build

Repeatable blueprint for adding a new feature to the MOM Generator, using gstack for the parts it already does well and project files for the parts specific to this repo.

## Steps

### 1. Clarify & plan (gstack)
- For a feature big enough to need product framing: `/office-hours` — clarify the problem.
- `/autoplan` — CEO + eng + design review of the approach.

### 2. Research (if needed)
If the feature requires an unfamiliar API capability, invoke `.claude/agents/researcher.md` with the specific question. Wait for findings before proceeding.

### 3. Design (project-specific)
- Invoke `.claude/agents/architect.md` to decide which files/modules to touch.
- Check `.claude/rules/architecture-boundaries.md` — does this need a new `*_auth.py`/`*_client.py` pair (a fourth meeting source), a new model, or does it extend an existing module?
- Check `.claude/memory/decisions.md` for relevant prior constraints.

### 4. Implement
- Add/update route in `app/routes.py`.
- Add/update helper in the appropriate module (`graph_client.py` / `zoom_client.py` / `google_client.py` / etc.), following `.claude/rules/api-conventions.md`.
- Add/update DB model in `app/models.py` if needed.
- Create/update template in `app/templates/`, extending `base.html`.
- Run: `flask db migrate -m "description" && flask db upgrade` if a model changed.

### 5. Review
- `/review` — gstack general code review.
- `/team-review` — this project's convention checklist (wrapper-module discipline, decode order, MOM shape).
- Invoke `.claude/agents/security-reviewer.md` if auth, tokens, or user input are involved.

### 6. Test
- Invoke `.claude/agents/test-writer.md` with the changed file paths and expected behavior.
- Run: `pytest tests/` and confirm passing.

### 7. QA & security (gstack)
- `/qa <staging-url>` — real browser test.
- `/cso` — security audit (only if security-sensitive).

### 8. Document
- Update `.claude/memory/progress.md` — move item from backlog to working.
- Update `.claude/memory/decisions.md` if an architectural decision was made.
- Update `.claude/memory/repository-map.md` if a new file was added.

### 9. Ship
- `/ship` — opens the PR (uses `.claude/rules/pr-standard.md` template via the `pr-description` skill).
