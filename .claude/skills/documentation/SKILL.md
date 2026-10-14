# Skill: Documentation

## Description
Triggered when the user asks to document a new feature, update `CLAUDE.md`/memory files after a change, or explain a module for a future contributor.

**Relationship to gstack**: gstack's `/document-release` and `/document-generate` produce release notes and generated docs. This skill keeps the *hand-written* project knowledge base (`.claude/memory/`, `CLAUDE.md`) accurate — the part no generator can infer, like why a decision was made.

## What this skill does
1. After a structural change, updates the relevant `.claude/memory/*.md` file:
   - New working feature → `.claude/memory/progress.md` (move from backlog to working).
   - New architectural choice → `.claude/memory/decisions.md` (decision + why + files touched).
   - New module/integration → `.claude/memory/repository-map.md` and, if it changes the request flow, `.claude/memory/architecture.md`.
   - New domain concept (a new MOM field, a new meeting-classification rule) → `.claude/memory/domain-knowledge.md`.
2. Keeps `CLAUDE.md`'s Tech Stack / Repository Navigation sections in sync when a new dependency or top-level module is added — but keeps the file under 300 lines.
3. Never duplicates information that's already derivable by reading the code (e.g. does not restate a function's full implementation) — links to the file instead.

## Style
- State the fact, then why it matters, then how to apply it — matches the existing `decisions.md`/`project-context.md` format.
- Prefer updating an existing memory file's relevant section over appending an unrelated new section.
- Mark inferred/assumed information explicitly (e.g. "assumption: ...") rather than presenting a guess as fact.

## When to hand off
For public-facing release notes or generated API docs, use gstack's `/document-release` / `/document-generate` instead — this skill is for the internal knowledge base only.
