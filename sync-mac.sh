#!/bin/bash
# Pull new bank data from SimpleFIN Bridge (README: "Automatic bank sync"). install-mac.sh runs this at
# 6:00 every morning and whenever the Mac starts up; each run catches up from every account's last good
# pull, so a missed morning or an outage fills in on its own. Log: data/simplefin-sync.log
# (Linux, Windows and Docker schedule `python run.py simplefin-sync` instead; see the README.)
set -uo pipefail
cd "$(dirname "$0")"

# data/ is its own git repo: snapshot the database around the sync, so a bad sync is one revert away.
# First `run.py backup-check`: a database that lost most of its transactions without an Erase everything on
# Manage data isn't committed as the newest backup (older ones are never replaced either way); the log says
# why, and --force-backup commits it anyway. After a real Erase everything, the commit before it is tagged
# before-erase-<date>, so it's easy to find.
FORCE_BACKUP=0
backup() {
  if [ -d data/.git ] && ! git -C data diff --quiet -- budget.db; then
    check=$(.venv/bin/python run.py backup-check); code=$?
    echo "$check"
    if [ $code -eq 3 ] && [ $FORCE_BACKUP -eq 0 ]; then
      return 0
    fi
    case "$check" in *"erased on purpose"*) git -C data tag -f "before-erase-$(date +%F)" HEAD >/dev/null ;; esac
    git -C data add budget.db && git -C data commit -q -m "$1" && echo "  data/ committed: $1"
  fi
}

# install-mac.sh runs this at 6:00 and then hourly until 21:00, so a Bridge outage is retried. Once today's
# sync has worked, later runs stop here: no Bridge request, no backup, nothing in the log. --force syncs anyway;
# --force-backup also commits a database the backup check held back.
if [ "${1:-}" = "--force-backup" ]; then
  FORCE_BACKUP=1
fi
if [ "${1:-}" != "--force" ] && [ $FORCE_BACKUP -eq 0 ] && .venv/bin/python run.py simplefin-synced-today; then
  exit 0
fi

backup "App edits before SimpleFIN sync $(date +%F)"
.venv/bin/python run.py simplefin-sync
status=$?
backup "SimpleFIN sync $(date +%F)"
exit $status
