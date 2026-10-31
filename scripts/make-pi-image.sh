#!/bin/bash
# Make a ready-to-flash Raspberry Pi OS image that sets itself up as a BalancePoint box on first boot
# (README: "A Raspberry Pi for someone else"). Runs on a Mac; needs no admin rights.
#
#   scripts/make-pi-image.sh <raspios-lite-arm64.img.xz> [output.img]
#
# Asks for the Wi-Fi, a Tailscale auth key and (optional) an SSH public key; or set them beforehand as
# PI_WIFI_NAME, PI_WIFI_PASSWORD, PI_TAILSCALE_KEY, PI_SSH_KEY_FILE, PI_HOSTNAME, PI_TIMEZONE, PI_COUNTRY.
# Flash the result with Raspberry Pi Imager (Operating System -> Use custom; say No to "OS customisation",
# which would replace these settings) or any image writer. The image holds the Wi-Fi password and the
# Tailscale key: delete it once the card is made.
set -euo pipefail
cd "$(dirname "$0")/.."

src=${1:?usage: scripts/make-pi-image.sh <raspios-lite-arm64.img.xz> [output.img]}
out=${2:-$HOME/Downloads/balancepoint-pi.img}  # not in the repo: it holds secrets
[ -f "$src" ] || { echo "No image at $src (download Raspberry Pi OS Lite 64-bit from raspberrypi.com)." >&2; exit 1; }
[ -e "$out" ] && { echo "$out already exists; remove it or pick another name." >&2; exit 1; }

ask() {  # ask VAR "question" default [secret]
  local var=$1 q=$2 def=${3:-} secret=${4:-} val
  [ -n "${!var:-}" ] && return
  if [ -n "$secret" ]; then read -r -s -p "$q: " val; echo; else read -r -p "$q${def:+ [$def]}: " val; fi
  printf -v "$var" '%s' "${val:-$def}"
}
mac_tz=$(readlink /etc/localtime | sed 's|.*/zoneinfo/||')
ask PI_HOSTNAME "Name on Tailscale (the phones open http://<name>)" budget
ask PI_WIFI_NAME "Wi-Fi name (exactly, capitals too; leave empty for Ethernet only)"
if [ -n "$PI_WIFI_NAME" ]; then
  ask PI_WIFI_PASSWORD "Wi-Fi password" "" secret
  if [ -z "${PI_WIFI_PASSWORD_CHECKED:-}" ] && [ -t 0 ]; then
    read -r -s -p "Wi-Fi password again: " again; echo
    [ "$again" = "$PI_WIFI_PASSWORD" ] || { echo "The passwords don't match." >&2; exit 1; }
  fi
fi
ask PI_COUNTRY "Wi-Fi country code" US
ask PI_TIMEZONE "Time zone" "$mac_tz"
ask PI_TAILSCALE_KEY "Tailscale auth key (admin console -> Settings -> Keys; empty to skip)" "" secret
ask PI_SSH_KEY_FILE "SSH public key for maintenance (empty: no SSH)" "$(ls ~/.ssh/id_ed25519.pub 2>/dev/null || true)"
[[ "$PI_HOSTNAME" =~ ^[a-z][a-z0-9-]{0,30}$ ]] || { echo "The name: lower-case letters, digits and hyphens." >&2; exit 1; }
[ -z "$PI_SSH_KEY_FILE" ] || [ -s "$PI_SSH_KEY_FILE" ] || { echo "No SSH key at $PI_SSH_KEY_FILE." >&2; exit 1; }
[ -n "$PI_WIFI_NAME" ] || echo "No Wi-Fi: the Pi needs an Ethernet cable."
[ -n "$PI_TAILSCALE_KEY" ] || echo "No Tailscale key: the app will only answer on the Pi itself until Tailscale is set up by hand."

echo "Unpacking $src..."
xz -dc "$src" > "$out"
dev=$(hdiutil attach -imagekey diskimage-class=CRawDiskImage -nomount "$out" | awk 'NR==1{print $1}')
trap 'hdiutil detach "$dev" >/dev/null 2>&1 || true' EXIT
boot=$(mktemp -d)
diskutil mount -mountPoint "$boot" "${dev}s1" >/dev/null
[ -f "$boot/cmdline.txt" ] && [ -f "$boot/user-data" ] || { echo "That doesn't look like a Raspberry Pi OS image with cloud-init (Trixie or later)." >&2; exit 1; }

echo "Writing the first-boot settings..."
mkdir -p "$boot/balancepoint"
cp pi/setup.sh pi/update.sh "$boot/balancepoint/"
printf 'APP_USER=budget\nREPO=%s\nPORT=80\n' "${PI_REPO:-https://github.com/AeroPoint/AP-BalancePoint.git}" > "$boot/balancepoint/settings.env"
[ -z "$PI_TAILSCALE_KEY" ] || printf '%s' "$PI_TAILSCALE_KEY" > "$boot/balancepoint/tailscale-authkey"
if [ -n "$PI_SSH_KEY_FILE" ]; then cp "$PI_SSH_KEY_FILE" "$boot/balancepoint/authorized_keys"; : > "$boot/ssh"; fi
# The image's placeholder user becomes "budget", with no password at all ("*" can't be typed): SSH is key-only.
echo 'budget:*' > "$boot/userconf.txt"

export PI_HOSTNAME PI_TIMEZONE PI_COUNTRY PI_WIFI_NAME PI_WIFI_PASSWORD=${PI_WIFI_PASSWORD:-}
PI_BOOT=$boot python3 - <<'EOF'
import json, os
q = json.dumps  # JSON strings are valid YAML, so any Wi-Fi name or password is quoted safely
e, boot = os.environ, os.environ["PI_BOOT"]
with open(f"{boot}/user-data", "w") as f:
    f.write(f"""#cloud-config
# BalancePoint first boot (scripts/make-pi-image.sh): name, time zone, then pi/setup.sh as a service that
# retries until it has worked.
hostname: {q(e["PI_HOSTNAME"])}
manage_etc_hosts: true
timezone: {q(e["PI_TIMEZONE"])}
write_files:
- path: /etc/systemd/system/balancepoint-setup.service
  content: |
    [Unit]
    Description=BalancePoint first-boot setup
    After=network-online.target userconfig.service
    Wants=network-online.target
    StartLimitIntervalSec=0

    [Service]
    Type=oneshot
    ExecStart=/bin/bash /boot/firmware/balancepoint/setup.sh
    Restart=on-failure
    RestartSec=60

    [Install]
    WantedBy=multi-user.target
runcmd:
- [systemctl, daemon-reload]
- [systemctl, enable, balancepoint-setup.service]
- [systemctl, start, --no-block, balancepoint-setup.service]
""")
net = ["network:", "  version: 2", "  ethernets:", "    eth0:", "      dhcp4: true", "      optional: true"]
if e.get("PI_WIFI_NAME"):
    net += ["  wifis:", "    wlan0:", "      dhcp4: true", "      optional: true",
            f"      regulatory-domain: {q(e['PI_COUNTRY'])}", "      access-points:",
            f"        {q(e['PI_WIFI_NAME'])}:", f"          password: {q(e['PI_WIFI_PASSWORD'])}"]
with open(f"{boot}/network-config", "w") as f:
    f.write("\n".join(net) + "\n")
EOF
chmod 755 "$boot/balancepoint/setup.sh" 2>/dev/null || true
diskutil unmount "$boot" >/dev/null
hdiutil detach "$dev" >/dev/null; trap - EXIT
echo
echo "Made $out. Flash it to a microSD card (or USB drive) with Raspberry Pi Imager: Choose OS -> Use custom,"
echo "and answer No to OS customisation. Then delete $out: it holds the Wi-Fi password and the Tailscale key."
echo "First boot takes about 15 minutes; then http://$PI_HOSTNAME opens on a phone with Tailscale on."
