#!/bin/bash
# PreToolUse hook — blocks commands that could expose secrets or destroy data.
# Exit 0 = allow. Exit 2 = block.

COMMAND="$1"

# Block any attempt to cat, echo, or print .env contents
if echo "$COMMAND" | grep -qE '(cat|echo|type|print)\s+.*\.env'; then
  echo "BLOCKED: Do not print .env contents." >&2
  exit 2
fi

# Block git add .env
if echo "$COMMAND" | grep -qE 'git\s+add\s+.*\.env'; then
  echo "BLOCKED: Do not stage .env files." >&2
  exit 2
fi

# Block force push to main
if echo "$COMMAND" | grep -qE 'git\s+push.*--force.*main|git\s+push.*-f.*main'; then
  echo "BLOCKED: Force push to main is not allowed." >&2
  exit 2
fi

# Block --no-verify (skip hooks)
if echo "$COMMAND" | grep -q '\-\-no\-verify'; then
  echo "BLOCKED: --no-verify is not allowed." >&2
  exit 2
fi

exit 0
