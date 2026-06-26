#!/bin/bash
# PostToolUse hook — runs after Edit/Write on Python files.
# Checks for common issues but does not auto-modify files.
# Exit 0 always (advisory only).

FILE="$1"

if [[ "$FILE" == *.py ]]; then
  # Warn if print() found in non-test file
  if [[ "$FILE" != tests/* ]] && grep -q 'print(' "$FILE" 2>/dev/null; then
    echo "WARNING: print() found in $FILE — use logger instead." >&2
  fi

  # Warn if bare except found
  if grep -qE '^\s+except\s*:' "$FILE" 2>/dev/null; then
    echo "WARNING: Bare except: found in $FILE — catch a specific exception." >&2
  fi
fi

if [[ "$FILE" == *.html ]]; then
  # Warn if CDN link found in template
  if grep -qi 'cdn\.jsdelivr\|cdnjs\.cloudflare\|unpkg\.com' "$FILE" 2>/dev/null; then
    echo "WARNING: CDN link found in $FILE — serve assets from app/static/ instead." >&2
  fi
fi

exit 0
