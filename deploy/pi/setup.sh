#!/usr/bin/env bash
# One-time setup of a Raspberry Pi (Ubuntu Server 24.04 64-bit, or 64-bit Raspberry Pi OS Lite) as a second Solis site:
# Docker, Tailscale, the repo (read-only deploy key), a generated .env, and the 3-minute auto-deploy.
# Run it as your normal user (not root), over SSH or at the console, either from a clone
#     git clone <repo> ~/solis-monitor && bash ~/solis-monitor/deploy/pi/setup.sh "Cottage"
# or as a single file copied over (scp): it then clones the repo itself.
# It is safe to run again; finished steps are skipped.
set -euo pipefail

SITE="${1:?usage: bash setup.sh \"<site name>\"}"
SLUG="${REPO_SLUG:-vtlvolkov/solis-monitor}"        # GitHub owner/repo
# Run from inside a clone (deploy/pi/setup.sh)? Then that clone is the install. Otherwise clone to ~/solis-monitor.
here="$(cd "$(dirname "$0")/../.." 2>/dev/null && pwd || true)"
if [[ -n "${SOLIS_DIR:-}" ]]; then DIR="$SOLIS_DIR"
elif [[ -f "$here/docker-compose.yml" && -d "$here/.git" ]]; then DIR="$here"
else DIR="$HOME/solis-monitor"; fi
TZ_NAME="${TZ_NAME:-Europe/Kyiv}"
[[ $EUID -ne 0 ]] || { echo "Run as a normal user with sudo, not as root." >&2; exit 1; }
[[ "$(uname -m)" == "aarch64" ]] || echo "Warning: $(uname -m) is not 64-bit ARM; a 64-bit OS is recommended."

# Ubuntu: unattended-upgrades holds the apt lock for a while after first boot, and needrestart
# would ask questions in the middle of an install.
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
APT=(sudo -E apt-get -o DPkg::Lock::Timeout=600 -y -qq)

echo "== 1/6 packages"
"${APT[@]}" update
# avahi: so that <host name>.local works on the LAN (Ubuntu Server doesn't resolve it by default)
"${APT[@]}" install git curl ca-certificates openssl python3 avahi-daemon >/dev/null
sudo timedatectl set-timezone "$TZ_NAME" || true

echo "== 2/6 Docker (the official package: it includes the buildx plugin the web image needs)"
if ! docker buildx version >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"
if [[ ! -f /etc/docker/daemon.json ]]; then          # keep logs from filling the SD card / disk
  echo '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}' | sudo tee /etc/docker/daemon.json >/dev/null
  sudo systemctl restart docker
fi
sudo systemctl enable docker >/dev/null

echo "== 3/6 Tailscale (log in when it prints a link; Tailscale SSH lets you log in without keys)"
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
if ! sudo tailscale status >/dev/null 2>&1; then
  ts_name="$(echo "$SITE" | tr 'A-Z ' 'a-z-' | tr -cd 'a-z0-9-')"      # Latin letters only; "Дача" -> use the host name
  [[ -n "$ts_name" ]] && ts_name="${ts_name}-solis" || ts_name="$(hostname)"
  sudo tailscale up --ssh --hostname="$ts_name"
fi
sudo tailscale status | sed -n 1,3p          # not head: it closes the pipe early and pipefail would stop the script

echo "== 4/6 repository (read-only deploy key)"
mkdir -p ~/.ssh && chmod 700 ~/.ssh
if [[ ! -f ~/.ssh/solis_deploy ]]; then
  ssh-keygen -q -t ed25519 -f ~/.ssh/solis_deploy -N "" -C "$(hostname) solis deploy"
fi
grep -q "^Host github-solis" ~/.ssh/config 2>/dev/null || cat >> ~/.ssh/config <<'CFG'
Host github-solis
  HostName github.com
  User git
  IdentityFile ~/.ssh/solis_deploy
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
CFG
chmod 600 ~/.ssh/config
github_auth_ok() {            # capture first: grep -q would close the pipe early and pipefail would report a failure
  local out; out=$(ssh -o BatchMode=yes -T git@github-solis 2>&1 || true)
  [[ "$out" == *"successfully authenticated"* ]]
}
until github_auth_ok; do
  echo
  echo "Add this public key at https://github.com/${SLUG}/settings/keys  (Add deploy key, leave 'Allow write access' OFF):"
  echo
  cat ~/.ssh/solis_deploy.pub
  echo
  read -rp "Press Enter once it's added (Ctrl+C to stop) " _
done
[[ -d "$DIR/.git" ]] || git clone "git@github-solis:${SLUG}.git" "$DIR"
# A clone made with your own credentials must switch to the deploy key, or the auto-deploy can't fetch.
git -C "$DIR" remote set-url origin "git@github-solis:${SLUG}.git"

echo "== 5/6 configuration"
cd "$DIR"
deploy/pi/make-env.sh "$SITE" "$TZ_NAME"

echo "== 6/6 auto-deploy every 3 minutes, then the first build (10-20 minutes on a Pi)"
for unit in solis-deploy.service solis-deploy.timer; do
  sed -e "s|^User=.*|User=$USER|" -e "s|/home/umbrel/solis-monitor|$DIR|" "deploy/$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now solis-deploy.timer
sg docker -c "deploy/deploy.sh --force"

cat <<MSG

Done. The dashboard is at http://$(hostname -I | awk '{print $1}'):8088 (and over Tailscale at http://$(tailscale ip -4 | head -1):8088).
It shows "no data" until the Pi reaches the site and the inverter's address is set:
  1. Plug the Pi into the router at the site (an Ethernet cable is the most reliable).
  2. Log in:  ssh $USER@$(hostname)   (from any device on your Tailscale network)
  3. Run:     ~/solis-monitor/deploy/pi/find-inverter.sh
Settings changes stay OFF (CONTROL_ENABLED=false in .env) until you've checked CURRENT_SCALE on site; see README, "Settings".
MSG
