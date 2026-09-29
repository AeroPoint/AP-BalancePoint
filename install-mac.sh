#!/bin/bash
# Make the budget app start whenever you log in to this Mac, and restart if it stops.
#   ./install-mac.sh            install (or update) and start it now
#   ./install-mac.sh remove     stop it and remove the auto-start
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
LABEL="local.ledger.budget"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "${1:-}" = "remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed. Start it by hand with ./start-mac.sh"
  exit 0
fi

case "$DIR" in
  "$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Downloads"*)
    echo "Move the Budget folder out of Desktop/Documents/Downloads first (e.g. to ~/Budget):"
    echo "macOS blocks background apps from those folders." ; exit 1 ;;
esac

chmod +x start-mac.sh
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
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 3
grep -m1 "On your phones" "$DIR/data/server.log" || echo "Started. Log: $DIR/data/server.log"
