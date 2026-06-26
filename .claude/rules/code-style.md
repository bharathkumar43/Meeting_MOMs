# Code Style Rules

## Python
- Follow PEP 8. Max line length: 100 characters.
- Use f-strings for string formatting. No `%` formatting or `.format()`.
- Type hints on all new functions. Return types required.
- No bare `except:` — always catch a specific exception or `Exception`.
- Use `logger = logging.getLogger(__name__)` at module top. No `print()` in production code.
- Guard imports: stdlib → third-party → local, separated by blank lines.

## Flask / Routes
- All routes live in `app/routes.py` under `main_bp` blueprint.
- Route functions named `snake_case`. Template names match route names where possible.
- Always validate `request.method` before accessing `request.form` or `request.json`.
- Flash messages use Bootstrap alert categories: `success`, `danger`, `warning`, `info`.

## HTML / Templates
- Extend `base.html` via `{% extends "base.html" %}`.
- Use `{% block content %}` and `{% block scripts %}` blocks.
- All static assets via `{{ url_for('static', filename='...') }}` — never hardcoded paths.
- No CDN links. All CSS/JS/fonts served from `app/static/`.

## JavaScript
- Vanilla JS only (no jQuery, no React). Bootstrap JS is loaded from local static.
- Place page-specific JS in `{% block scripts %}` or `app/static/js/`.
- Use `fetch()` for AJAX calls. Handle errors explicitly.

## CSS
- Prefer Bootstrap utility classes over custom CSS.
- Custom overrides go in `app/static/css/style.css` only.
- No inline `style=` attributes unless dynamically set by JS.
