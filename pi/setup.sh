#!/bin/bash
# First-boot setup of a BalancePoint box on Raspberry Pi OS (README: "A Raspberry Pi for someone else").
# scripts/make-pi-card.sh puts this on the card's boot partition; cloud-init starts it once, as the
# balancepoint-setup service, which retries every minute until it has worked (slow Wi-Fi, a mirror hiccup).
# Every step can run again safely. Progress goes to /var/log/balancepoint-setup.log and, so it can be read
# by putting the card in any computer, to balancepoint/setup.log on the boot partition.
set -euo pipefail

CARD=/boot/firmware/balancepoint
. "$CARD/settings.env"                     # APP_USER, REPO, PORT
APP="/home/$APP_USER/BalancePoint"
DONE=/var/lib/balancepoint/setup-done
exec > >(tee -a /var/log/balancepoint-setup.log "$CARD/setup.log") 2>&1
echo "== $(date '+%F %T') setup"
[ -e "$DONE" ] && { echo "Already set up."; exit 0; }

as_app() { runuser -u "$APP_USER" -- "$@"; }
id "$APP_USER" >/dev/null 2>&1 || { echo "User $APP_USER not there yet (userconf.txt runs first); trying again soon."; exit 1; }

echo "Waiting for the internet..."
for _ in $(seq 60); do curl -fsS -o /dev/null https://github.com && break; sleep 10; done
curl -fsS -o /dev/null https://github.com || { echo "No internet yet; trying again soon."; exit 1; }

echo "Installing packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git python3-venv unattended-upgrades   # unattended-upgrades: security updates on their own

# Tailscale from its own signed package repository (the same steps as https://tailscale.com/install.sh).
if ! command -v tailscale >/dev/null; then
  echo "Installing Tailscale..."
  . /etc/os-release
  curl -fsSL "https://pkgs.tailscale.com/stable/debian/$VERSION_CODENAME.noarmor.gpg" -o /usr/share/keyrings/tailscale-archive-keyring.gpg
  curl -fsSL "https://pkgs.tailscale.com/stable/debian/$VERSION_CODENAME.tailscale-keyring.list" -o /etc/apt/sources.list.d/tailscale.list
  apt-get update -q
  apt-get install -y -q tailscale
fi
if [ -s "$CARD/tailscale-authkey" ]; then
  echo "Joining Tailscale as $(hostname)..."
  tailscale up --auth-key="file:$CARD/tailscale-authkey" --hostname="$(hostname)"
  shred -u "$CARD/tailscale-authkey"   # used up; nothing left on the card to join with
fi

# SSH only with the key(s) on the card, never a password. No key on the card: SSH stays off.
if [ -s "$CARD/authorized_keys" ]; then
  install -d -m 700 -o "$APP_USER" -g "$APP_USER" "/home/$APP_USER/.ssh"
  install -m 600 -o "$APP_USER" -g "$APP_USER" "$CARD/authorized_keys" "/home/$APP_USER/.ssh/authorized_keys"
  printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n' > /etc/ssh/sshd_config.d/10-keys-only.conf
  systemctl reload ssh 2>/dev/null || true
fi

echo "Installing BalancePoint..."
[ -d "$APP/.git" ] || as_app git clone -q "$REPO" "$APP"
tag=$(as_app git -C "$APP" tag -l 'v*' --sort=-v:refname | head -1)
[ -n "$tag" ] && as_app git -C "$APP" checkout -q "$tag"
echo "Version: ${tag:-main}"
[ -x "$APP/.venv/bin/python" ] || as_app python3 -m venv "$APP/.venv"
as_app "$APP/.venv/bin/python" -m pip install -q -r "$APP/requirements.txt"
if [ ! -d "$APP/data/.git" ]; then   # data/ is its own git repo: snapshots before and after syncs and updates
  as_app mkdir -p "$APP/data"
  as_app git -C "$APP/data" init -q
  as_app git -C "$APP/data" config user.name "BalancePoint"
  as_app git -C "$APP/data" config user.email "balancepoint@$(hostname)"
fi
install -m 755 "$CARD/update.sh" /usr/local/sbin/balancepoint-update

# A SimpleFIN setup token on the card: claim it (it works once) and keep only the access, as Bank sync would.
# Exit 2 = never reached SimpleFIN, so the token is still good: keep it and let the service try again.
if [ -s "$CARD/simplefin-token" ]; then
  echo "Connecting the bank sync..."
  rc=0
  (cd "$APP" && as_app .venv/bin/python -c '
import sys
from budget import simplefin_import as sf
try:
    sf.save_access_url(sys.argv[1], sf.claim_setup_token(sys.stdin.read()))
except sf.SimpleFinError as exc:
    print(exc)
    sys.exit(2 if str(exc).startswith(("Couldn'"'"'t reach", "Cloudflare blocked")) else 1)
print("Bank sync connected; pick which account is which on the Bank sync page.")
' "$APP/data" < "$CARD/simplefin-token") || rc=$?
  [ "$rc" = 2 ] && { echo "Trying the bank sync again soon."; exit 1; }
  [ "$rc" = 0 ] || echo "Make a new setup token at bridge.simplefin.org and paste it on the Bank sync page."
  shred -u "$CARD/simplefin-token"
fi

echo "Starting services..."
cat > /etc/systemd/system/balancepoint.service <<EOF
[Unit]
Description=BalancePoint budget app
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
User=$APP_USER
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/python run.py serve --phones --no-browser --port $PORT
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
# The bank sync: 6:00, then hourly until 21:00 only if it hasn't worked yet today (sync-mac.sh is plain bash).
cat > /etc/systemd/system/balancepoint-sync.service <<EOF
[Unit]
Description=BalancePoint bank sync
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$APP_USER
WorkingDirectory=$APP
ExecStart=/bin/sh -c './sync-mac.sh >> data/simplefin-sync.log 2>&1'
EOF
cat > /etc/systemd/system/balancepoint-sync.timer <<EOF
[Unit]
Description=BalancePoint bank sync, daily at 6:00 with hourly retries

[Timer]
OnCalendar=*-*-* 06..21:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
cat > /etc/systemd/system/balancepoint-update.service <<EOF
[Unit]
Description=Move BalancePoint to its newest release
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=APP_USER=$APP_USER APP=$APP PORT=$PORT
ExecStart=/usr/local/sbin/balancepoint-update
EOF
cat > /etc/systemd/system/balancepoint-update.timer <<EOF
[Unit]
Description=Weekly BalancePoint update check

[Timer]
OnCalendar=Sun *-*-* 03:30
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now balancepoint.service balancepoint-sync.timer balancepoint-update.timer

mkdir -p "$(dirname "$DONE")" && date > "$DONE"
systemctl disable balancepoint-setup.service 2>/dev/null || true
echo "== $(date '+%F %T') done. Open http://$(hostname)$([ "$PORT" = 80 ] || echo ":$PORT") on a phone with Tailscale on."
