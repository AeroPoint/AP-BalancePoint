#!/bin/bash
# Pull new bank data from SimpleFIN Bridge (README: "Automatic bank sync"). install-mac.sh runs this at
# 6:00 every morning and whenever the Mac starts up; each run catches up from every account's last good
# pull, so a missed morning or an outage fills in on its own. Log: data/simplefin-sync.log
# (Linux, Windows and Docker schedule `python run.py simplefin-sync` instead; see the README.)
set -uo pipefail
cd "$(dirname "$0")"

# data/ is its own git repo: snapshot the database around the sync, so a bad sync is one revert away.
backup() {
  if [ -d data/.git ] && ! git -C data diff --quiet -- budget.db; then
    git -C data add budget.db && git -C data commit -q -m "$1" && echo "  data/ committed: $1"
  fi
}

backup "App edits before SimpleFIN sync $(date +%F)"
.venv/bin/python run.py simplefin-sync
status=$?
backup "SimpleFIN sync $(date +%F)"
exit $status
