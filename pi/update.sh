#!/bin/bash
# Weekly on a BalancePoint Raspberry Pi (pi/setup.sh installs it as /usr/local/sbin/balancepoint-update):
# move to the newest release tag, if there's one, and go back to the old one if the app won't start on it.
# Only release tags are followed, never unreleased work. data/ is committed first, so the data can be rolled
# back too. Runs as root (to restart the app); git and pip run as the app's user. Log: journalctl -u balancepoint-update
set -uo pipefail
: "${APP_USER:?}" "${APP:?}" "${PORT:=80}"
as_app() { runuser -u "$APP_USER" -- "$@"; }
cd "$APP" || exit 1

as_app git fetch -q --tags origin || { echo "Couldn't reach GitHub; trying next week."; exit 0; }
new=$(as_app git tag -l 'v*' --sort=-v:refname | head -1)
old=$(as_app git describe --tags --exact-match 2>/dev/null || as_app git rev-parse HEAD)
[ -n "$new" ] && [ "$new" != "$old" ] || { echo "Up to date ($old)."; exit 0; }

if [ -d data/.git ] && [ -n "$(as_app git -C data status --porcelain)" ]; then
  as_app git -C data add -A && as_app git -C data commit -q -m "Before updating to $new"
fi

switch_to() {
  as_app git checkout -q "$1" && as_app .venv/bin/python -m pip install -q -r requirements.txt \
    && systemctl restart balancepoint && for _ in $(seq 30); do
      curl -fsS -o /dev/null "http://127.0.0.1:$PORT/" && return 0; sleep 2; done
  return 1
}

echo "Updating $old -> $new"
if switch_to "$new"; then
  echo "Now on $new."
else
  echo "The app didn't start on $new; going back to $old."
  switch_to "$old" || echo "It doesn't start on $old either; needs a look."
  exit 1
fi
