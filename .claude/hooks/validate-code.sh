#!/bin/bash
# PreToolUse hook — validates code before it is committed.
# Called with the staged file path as $1.
# Exit 0 = allow. Exit 2 = block.

FILE="$1"

# Only validate Python files
if [[ "$FILE" != *.py ]]; then
  exit 0
fi

# Check Python syntax
if command -v python3 &>/dev/null; then
  python3 -m py_compile "$FILE" 2>&1
  if [ $? -ne 0 ]; then
    echo "BLOCKED: Syntax error in $FILE" >&2
    exit 2
  fi
fi

exit 0
