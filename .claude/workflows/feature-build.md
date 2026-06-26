# Workflow: Feature Build

Repeatable blueprint for adding a new feature to the MOM Generator.

## Steps

### 1. Research (if needed)
If the feature requires an unfamiliar API capability:
- Invoke `@research` agent with the specific question.
- Wait for findings before proceeding.

### 2. Design
- Define the route(s) needed and their HTTP methods.
- Identify which existing modules to extend (`graph_client.py`, `zoom_client.py`, etc.).
- Check `decisions.md` for relevant constraints.

### 3. Implement
- Add/update route in `app/routes.py`.
- Add/update helper in the appropriate module.
- Add/update DB model in `app/models.py` if needed.
- Create/update template in `app/templates/`.
- Run: `flask db migrate -m "description" && flask db upgrade` if model changed.

### 4. Review
- Run `/review` on changed files.
- Invoke `@security-reviewer` if auth, tokens, or user input involved.

### 5. Test
- Invoke `@test-writer` with the changed file paths and expected behavior.
- Run: `pytest tests/` and confirm passing.

### 6. Document
- Update `.claude/memory/progress.md` — move item from backlog to working.
- Update `.claude/memory/decisions.md` if an architectural decision was made.

### 7. Commit & Push
```bash
git add <specific files>
git commit -m "feat: <description>"
git push origin main
```
