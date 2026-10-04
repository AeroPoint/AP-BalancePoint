#!/bin/bash
# Start the budget app on a Mac, reachable from your phones over Tailscale (README: "Optional: keep it running on an always-on Mac").
# The app runs from its own environment in .venv, so it doesn't depend on conda or whatever "python"
# means in a given shell. First run (or a broken .venv) builds it from the first Python 3.11+ found:
# $BUDGET_PYTHON if set, else Homebrew's python@3.12 (Apple silicon, then Intel), else python3 on PATH.
set -euo pipefail
cd "$(dirname "$0")"

new_enough() { "$1" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; }

find_python() {
  if [ -n "${BUDGET_PYTHON:-}" ]; then
    [ -x "$BUDGET_PYTHON" ] || command -v "$BUDGET_PYTHON" >/dev/null 2>&1 || { echo "BUDGET_PYTHON=$BUDGET_PYTHON not found." >&2; return 1; }
    new_enough "$BUDGET_PYTHON" || { echo "$BUDGET_PYTHON is older than Python 3.11." >&2; return 1; }
    echo "$BUDGET_PYTHON"; return 0
  fi
  local p
  for p in /opt/homebrew/opt/python@3.12/bin/python3.12 /usr/local/opt/python@3.12/bin/python3.12; do
    if [ -x "$p" ] && new_enough "$p"; then echo "$p"; return 0; fi
  done
  p="$(command -v python3 || true)"
  if [ -n "$p" ] && new_enough "$p"; then echo "$p"; return 0; fi
  echo "Needs Python 3.11 or newer. Install it (brew install python@3.12, or python.org), or set BUDGET_PYTHON to one." >&2
  return 1
}

if ! .venv/bin/python -c 'import flask, openpyxl' 2>/dev/null; then
  echo "Setting up Python environment in .venv..."
  BASE="$(find_python)" || exit 1
  echo "Using $BASE"
  rm -rf .venv
  "$BASE" -m venv .venv
  .venv/bin/python -m pip install --quiet -r requirements.txt
fi
exec .venv/bin/python run.py serve --phones --no-browser "$@"
