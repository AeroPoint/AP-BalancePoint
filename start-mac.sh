#!/bin/bash
# Start the budget app on a Mac, reachable from your phones over Tailscale (README: "On a Mac").
# The app runs from its own environment in .venv, built from Homebrew's python@3.12 so it doesn't
# depend on conda or whatever "python" means in a given shell. First run (or a broken .venv) builds it.
set -euo pipefail
cd "$(dirname "$0")"
BASE="${BUDGET_PYTHON:-/opt/homebrew/opt/python@3.12/bin/python3.12}"
if ! .venv/bin/python -c 'import flask, openpyxl' 2>/dev/null; then
  echo "Setting up Python environment in .venv..."
  if [ ! -x "$BASE" ]; then
    echo "Needs Homebrew's Python: brew install python@3.12 (or set BUDGET_PYTHON to a Python 3.11+)."; exit 1
  fi
  "$BASE" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "$BASE is older than Python 3.11."; exit 1; }
  rm -rf .venv
  "$BASE" -m venv .venv
  .venv/bin/python -m pip install --quiet -r requirements.txt
fi
exec .venv/bin/python run.py serve --phones --no-browser "$@"
