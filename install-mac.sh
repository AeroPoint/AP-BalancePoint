#!/bin/bash
# Make the budget app start whenever you log in to this Mac, and restart if it stops. Also schedules
# the daily bank sync (sync-mac.sh) at 6:00 and at every startup. Needs Python 3.11 or newer: Homebrew's
# python@3.12, a python.org install, or BUDGET_PYTHON pointing at one (start-mac.sh picks it).
#   ./install-mac.sh            install (or update) and start it now
#   ./install-mac.sh remove     stop both and remove the auto-start
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
LABEL="local.ledger.budget"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SYNC_LABEL="local.ledger.simplefin-sync"
SYNC_PLIST="$HOME/Library/LaunchAgents/$SYNC_LABEL.plist"

if [ "${1:-}" = "remove" ]; then
  for l in "$LABEL" "$SYNC_LABEL"; do launchctl bootout "gui/$(id -u)/$l" 2>/dev/null || true; done
  rm -f "$PLIST" "$SYNC_PLIST"
  echo "Removed. Start it by hand with ./start-mac.sh; sync by hand with ./sync-mac.sh"
  exit 0
fi

case "$DIR" in
  "$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Downloads"*)
    echo "Move the app folder out of Desktop/Documents/Downloads first (e.g. to ~/BalancePoint):"
    echo "macOS blocks background apps from those folders." ; exit 1 ;;
esac

chmod +x start-mac.sh sync-mac.sh
./start-mac.sh --help >/dev/null 2>&1 || true   # builds .venv on first run
mkdir -p "$HOME/Library/LaunchAgents" "$DIR/data"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$DIR/start-mac.sh</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/data/server.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/server.log</string>
</dict>
</plist>
EOF
# 6:00 is after the banks' overnight posting and before anyone opens the app. If the Mac was asleep
# then, launchd runs it on wake; RunAtLoad also runs it after a restart or power cut.
cat > "$SYNC_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$SYNC_LABEL</string>
  <key>ProgramArguments</key><array><string>$DIR/sync-mac.sh</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>0</integer></dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DIR/data/simplefin-sync.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/simplefin-sync.log</string>
</dict>
</plist>
EOF
for pair in "$LABEL:$PLIST" "$SYNC_LABEL:$SYNC_PLIST"; do
  target="gui/$(id -u)/${pair%%:*}"
  launchctl bootout "$target" 2>/dev/null || true
  # bootout finishes in the background; loading the same label before it's gone fails with error 5.
  for _ in $(seq 20); do launchctl print "$target" >/dev/null 2>&1 || break; sleep 0.5; done
  launchctl bootstrap "gui/$(id -u)" "${pair#*:}"
done
sleep 3
grep -m1 "On your phones" "$DIR/data/server.log" || echo "Started. Log: $DIR/data/server.log"
echo "Bank sync: daily at 6:00 and at startup. Log: $DIR/data/simplefin-sync.log"
