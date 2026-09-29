#!/bin/bash
# Start the budget app on a Mac, reachable from your phones over Tailscale (README: "On a Mac").
# First run creates the Python environment. Needs Python 3.11 or newer.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Setting up Python environment..."
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "Python 3.11+ is needed (python.org or Homebrew)."; exit 1; }
  python3 -m venv .venv
  .venv/bin/python -m pip install --quiet -r requirements.txt
fi
exec .venv/bin/python run.py serve --phones --no-browser "$@"
