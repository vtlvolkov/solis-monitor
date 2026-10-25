#!/usr/bin/env bash
# Solis Monitor installer: a dashboard for a Solis hybrid inverter, running in Docker on this machine.
#
#   curl -fsSL https://github.com/vtlvolkov/solis-monitor/releases/latest/download/install.sh | sudo bash
#
# It asks for a dashboard user name and password and finds the inverter's data logger on your network,
# installs Docker if it is missing, downloads the prebuilt images and starts everything.
# Debian, Ubuntu and Raspberry Pi OS (64-bit ARM or x86-64). Changing inverter settings stays OFF.
#
# Options (all optional; with none it asks):
#   --user NAME           dashboard user name (default: admin)
#   --password PASS       dashboard password (default: asked, or generated when not interactive)
#   --inverter IP         inverter data logger address (default: scan this network)
#   --scan-network A.B.C  scan A.B.C.1-254 instead of this machine's own network
#   --no-scan             do not look for the inverter (set INVERTER_HOST in .env later)
#   --site-name NAME      title of the dashboard and prefix of alerts (default: none)
#   --timezone ZONE       e.g. Europe/Kyiv (default: this machine's)
#   --port N              dashboard port (default: 8088)
#   --protect-dashboard   ask for the password for the whole dashboard, not only for Settings
#   --auto-updates        install new tagged releases every night (default: update by hand)
#   --version vX.Y.Z      install this release (default: the release this script belongs to)
#   --dir PATH            install here (default: /opt/solis-monitor)
#   --yes                 never ask questions, use the defaults
#   --dry-run             show what would be done and change nothing
#   -h, --help            this text
set -euo pipefail

REPO="vtlvolkov/solis-monitor"          # GitHub owner/repo
DEFAULT_VERSION=""                      # stamped with the release tag by the release workflow
RAW_BASE="${SOLIS_RAW_BASE:-https://raw.githubusercontent.com/${REPO}}"
API_BASE="${SOLIS_API_BASE:-https://api.github.com/repos/${REPO}}"
# SOLIS_TEST_MODE=1 is for the test suite: it skips root, OS, apt, Docker and systemd steps.
TEST_MODE="${SOLIS_TEST_MODE:-}"

DIR="${SOLIS_DIR:-/opt/solis-monitor}"
VERSION="${SOLIS_VERSION:-$DEFAULT_VERSION}"
USER_NAME="" PASS="" INVERTER="" SCAN_PREFIX="" SITE="" TZ_NAME="" PORT=8088
PROTECT_ALL="" AUTO_UPDATES="" ASSUME_YES="" DRY_RUN="" NO_SCAN=""
MODBUS_PORT="${INVERTER_PORT:-502}"
MODBUS_UNIT="${MODBUS_UNIT:-1}"

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
warn() { printf 'Warning: %s\n' "$*" >&2; }
die() { printf '\nInstall stopped: %s\n' "$*" >&2; exit 1; }
usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; }

while (($#)); do
  case "$1" in
    --user) USER_NAME="${2:?--user needs a value}"; shift 2 ;;
    --password) PASS="${2:?--password needs a value}"; shift 2 ;;
    --inverter) INVERTER="${2:?--inverter needs an address}"; shift 2 ;;
    --scan-network) SCAN_PREFIX="${2:?--scan-network needs A.B.C}"; shift 2 ;;
    --no-scan) NO_SCAN=1; shift ;;
    --site-name) SITE="${2:?--site-name needs a value}"; shift 2 ;;
    --timezone) TZ_NAME="${2:?--timezone needs a value}"; shift 2 ;;
    --port) PORT="${2:?--port needs a number}"; shift 2 ;;
    --protect-dashboard) PROTECT_ALL=1; shift ;;
    --auto-updates) AUTO_UPDATES=1; shift ;;
    --version) VERSION="${2:?--version needs a tag}"; shift 2 ;;
    --dir) DIR="${2:?--dir needs a path}"; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
done

trap 'rc=$?; ((rc)) && printf "\nInstall failed (line %s). Nothing is lost: fix the cause and run the same command again.\n" "$LINENO" >&2' EXIT

# ---------------------------------------------------------------- questions
# Questions go to the terminal even when the script itself is piped in (curl | sudo bash).
interactive=""
if [[ -z "$ASSUME_YES" ]] && (: </dev/tty) 2>/dev/null; then interactive=1; fi
ask() {        # ask VAR "question" [default]: leaves VAR alone when not interactive
  local var="$1" q="$2" def="${3:-}" ans
  [[ -n "$interactive" ]] || return 0
  read -rp "$q${def:+ [$def]}: " ans </dev/tty
  printf -v "$var" '%s' "${ans:-$def}"
}
ask_secret() { # ask_secret VAR "question"
  local var="$1" ans
  read -rsp "$2: " ans </dev/tty; echo >&2
  printf -v "$var" '%s' "$ans"
}
yes_no() {     # yes_no "question" default(y|n)
  local ans def="$2"
  [[ -n "$interactive" ]] || { [[ "$def" == y ]]; return; }
  read -rp "$1 [$([[ $def == y ]] && echo Y/n || echo y/N)] " ans </dev/tty
  ans="${ans:-$def}"; [[ "$ans" == [yY]* ]]
}

valid_user() { [[ "$1" =~ ^[A-Za-z0-9._-]{1,32}$ ]]; }
valid_pass() { [[ ${#1} -ge 8 && ${#1} -le 128 && "$1" =~ ^[A-Za-z0-9._~!@%^*+=,/?-]+$ ]]; }
valid_ip() {
  local ip="$1" o IFS=.
  [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  for o in $ip; do ((10#$o <= 255)) || return 1; done
}
PASS_RULE="at least 8 characters: letters, digits and . _ ~ ! @ % ^ * + = , / ? -  (no spaces, quotes, \$ or #, which .env files treat specially)"
random_hex() { openssl rand -hex "$1"; }

# ---------------------------------------------------------------- preflight
step "Checking this machine"
if [[ -z "$TEST_MODE" ]]; then
  [[ $EUID -eq 0 ]] || die "run it as root:  curl -fsSL <link> | sudo bash"
  [[ -r /etc/os-release ]] || die "cannot tell which Linux this is (no /etc/os-release)"
  os_name=$(. /etc/os-release; echo "${PRETTY_NAME:-unknown}")      # in a subshell: os-release also defines VERSION
  case " $(. /etc/os-release; echo "${ID:-} ${ID_LIKE:-}") " in
    *" debian "*|*" ubuntu "*|*" raspbian "*) ;;
    *) die "this installer supports Debian, Ubuntu and Raspberry Pi OS; this is $os_name. See the README for a manual install." ;;
  esac
  [[ -d /run/systemd/system ]] || die "systemd is required"
  case "$(uname -m)" in
    x86_64|aarch64) ;;
    *) die "the images are built for 64-bit systems (x86-64 and ARM64); this is $(uname -m). On a Raspberry Pi use the 64-bit OS." ;;
  esac
  say "$os_name, $(uname -m)"
fi
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT >= 1 && PORT <= 65535)) || die "--port must be a number from 1 to 65535"
[[ "$MODBUS_PORT" =~ ^[0-9]+$ ]] || die "INVERTER_PORT must be a number"

if [[ -z "$DRY_RUN" && -z "$TEST_MODE" ]]; then
  export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
  apt-get -o DPkg::Lock::Timeout=600 -y -qq update >/dev/null
  apt-get -o DPkg::Lock::Timeout=600 -y -qq install curl ca-certificates openssl python3 >/dev/null
fi
for tool in curl openssl python3; do command -v "$tool" >/dev/null || die "$tool is missing (install it and run again)"; done

if [[ -e "$DIR/.env" ]]; then
  die "Solis Monitor is already installed in $DIR. Update it with:  solis-monitor update
To start from scratch (this DELETES the stored history): cd $DIR && docker compose down -v && rm -rf $DIR"
fi
if [[ -z "$TEST_MODE" ]] && command -v ss >/dev/null && ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN; then
  die "port $PORT is already in use. Choose another with --port."
fi

# ---------------------------------------------------------------- which release
if [[ -z "$VERSION" ]]; then
  json=$(curl -fsS -m 20 "$API_BASE/releases/latest") || die "could not look up the latest release (is there one yet? use --version vX.Y.Z)"
  VERSION=$(printf '%s' "$json" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | sed -n 1p)
  [[ -n "$VERSION" ]] || die "no release found; use --version vX.Y.Z"
fi
[[ "$VERSION" =~ ^[A-Za-z0-9._-]+$ ]] || die "odd version '$VERSION'"
say "Release: $VERSION"

# ---------------------------------------------------------------- dashboard login
step "Dashboard login"
say "The dashboard is open to anyone on your network. The password protects the Settings page"
say "(and the whole dashboard with --protect-dashboard)."
ask USER_NAME "Admin user name" admin
USER_NAME="${USER_NAME:-admin}"
valid_user "$USER_NAME" || die "user name: 1-32 letters, digits, . _ -"
generated=""
if [[ -n "$PASS" ]]; then
  valid_pass "$PASS" || die "password: $PASS_RULE"
elif [[ -n "$interactive" ]]; then
  say "Password: $PASS_RULE. Leave empty to generate one."
  while :; do
    ask_secret PASS "Admin password"
    if [[ -z "$PASS" ]]; then break; fi
    valid_pass "$PASS" || { say "  Not accepted: $PASS_RULE"; continue; }
    again=""
    ask_secret again "Repeat the password"
    [[ "$PASS" == "$again" ]] && break
    say "  The two did not match, try again."
  done
fi
if [[ -z "$PASS" ]]; then PASS="$(random_hex 8)"; generated=1; fi

ask SITE "Name of this installation (shown as the dashboard title; Enter to skip)" ""

# ---------------------------------------------------------------- inverter
step "Inverter"
say "The inverter's data logger (the WiFi/Ethernet stick) must be on this network. It accepts only"
say "one connection at a time: stop any other program that reads it (Home Assistant, a second"
say "monitor) before or right after this install."

probe() {      # does $1:port answer a Modbus request?
  python3 -I - "$1" "$MODBUS_PORT" "$MODBUS_UNIT" <<'PY'
import socket, struct, sys
host, port, unit = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
try:
    s = socket.create_connection((host, port), timeout=3)
    s.sendall(struct.pack(">HHHBBHH", 1, 0, 6, unit, 4, 33000, 1))
    r = s.recv(64)
    sys.exit(0 if len(r) >= 9 and r[:4] == b"\x00\x01\x00\x00" and r[7] & 0x7F == 4 else 1)
except Exception:
    sys.exit(1)
PY
}

scan() {       # prints the addresses in a.b.c.1-254 that speak Modbus
  local prefix="$1" me="$2" open h
  open=$(seq 1 254 | xargs -P 100 -I{} sh -c \
    "timeout 1 bash -c 'echo > /dev/tcp/${prefix}.{}/${MODBUS_PORT}' 2>/dev/null && echo ${prefix}.{}" | sort -t. -k4 -n || true)
  for h in $open; do
    [[ "$h" == "$me" ]] && continue
    if probe "$h"; then echo "$h"; else echo "  $h has port ${MODBUS_PORT} open but did not answer Modbus: skipped" >&2; fi
  done
}

if [[ -n "$INVERTER" ]]; then
  valid_ip "$INVERTER" || die "--inverter must be an IPv4 address like 192.168.1.50"
else
  if [[ -z "$SCAN_PREFIX" ]]; then
    me=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | sed -n 1p || true)
  else
    me=""
  fi
  prefix="${SCAN_PREFIX:-${me%.*}}"
  if [[ -z "$prefix" ]]; then
    warn "no network route to scan"
  elif [[ -n "$NO_SCAN" ]] || ! yes_no "Look for the inverter on ${prefix}.0/24 now?" y; then
    say "Not scanning."
  else
    say "Scanning ${prefix}.1-254 for Modbus TCP on port ${MODBUS_PORT} (takes a few seconds) ..."
    mapfile -t found < <(scan "$prefix" "${me:-}")
    if ((${#found[@]} == 1)); then
      INVERTER="${found[0]}"; say "Found a Modbus device at $INVERTER"
      yes_no "Is that your inverter's data logger?" y || INVERTER=""      # no: ask for the address below
    elif ((${#found[@]} > 1)); then
      say "Several Modbus devices answered:"
      for i in "${!found[@]}"; do say "  $((i + 1))) ${found[$i]}"; done
      if [[ -n "$interactive" ]]; then
        pick=""; ask pick "Which one is the inverter's logger? (number, or 0 to type the address yourself)" 1
        INVERTER=""
        if [[ "$pick" =~ ^[1-9][0-9]*$ ]]; then INVERTER="${found[$((pick - 1))]:-}"; fi
      else
        warn "not choosing for you; pass --inverter IP"
      fi
    else
      say "No Modbus device answered on ${prefix}.0/24."
      say "  - Is the data logger powered and online (check the Solis app)? Is this machine on the same network?"
      say "  - The logger accepts only ONE connection at a time. If another program is connected to it"
      say "    (Home Assistant, another monitor, a second copy of this stack), it looks absent here: stop that first."
    fi
  fi
  if [[ -z "$INVERTER" ]]; then
    ask INVERTER "Inverter logger IP address (Enter to set it later in $DIR/.env)" ""
    [[ -z "$INVERTER" ]] || valid_ip "$INVERTER" || die "that is not an IPv4 address"
  fi
fi
if [[ -n "$INVERTER" ]] && ! probe "$INVERTER"; then
  warn "$INVERTER:$MODBUS_PORT did not answer a Modbus request. Installing anyway: check the address, the logger's network and that nothing else is connected to it."
fi

if [[ -z "$TZ_NAME" ]]; then
  TZ_NAME=$(timedatectl show -p Timezone --value 2>/dev/null || true)
  [[ -n "$TZ_NAME" && "$TZ_NAME" != "n/a" ]] || TZ_NAME=$(cat /etc/timezone 2>/dev/null || true)
  TZ_NAME="${TZ_NAME:-UTC}"
fi
[[ "$TZ_NAME" =~ ^[A-Za-z0-9_+/-]+$ ]] || die "odd time zone '$TZ_NAME'"
[[ "$SITE" != *[\$\#\"\'\\\`\&\|]* && "${#SITE}" -le 40 ]] || die "site name: up to 40 characters, none of  \" ' \\ \` \$ # & |"

# ---------------------------------------------------------------- plan
owner="$(printf '%s' "${REPO%%/*}" | tr 'A-Z' 'a-z')"
IMAGE_PREFIX="ghcr.io/${owner}/solis-monitor"
step "Plan"
cat <<PLAN
  Release        $VERSION  (images $IMAGE_PREFIX-poller / -web)
  Install in     $DIR
  Dashboard      port $PORT, login ${USER_NAME} / $([[ -n "$generated" ]] && echo "(generated)" || echo "(your password)"), $([[ -n "$PROTECT_ALL" ]] && echo "whole dashboard protected" || echo "password only for Settings")
  Inverter       ${INVERTER:-not set yet} (port $MODBUS_PORT)
  Time zone      $TZ_NAME
  Inverter settings can be changed from the dashboard: NO (off; see the README before enabling)
  Updates        $([[ -n "$AUTO_UPDATES" ]] && echo "automatic, tagged releases, nightly" || echo "by hand: solis-monitor update")
PLAN
if [[ -n "$DRY_RUN" ]]; then say; say "Dry run: nothing was changed."; trap - EXIT; exit 0; fi
if [[ -n "$interactive" ]]; then yes_no "Install now?" y || { say "Cancelled."; trap - EXIT; exit 0; }; fi

# ---------------------------------------------------------------- Docker
if [[ -z "$TEST_MODE" ]]; then
  step "Docker"
  if ! docker compose version >/dev/null 2>&1; then
    if command -v docker >/dev/null; then
      apt-get -y -qq install docker-compose-plugin >/dev/null
    else
      curl -fsSL https://get.docker.com | sh
    fi
  fi
  if [[ ! -f /etc/docker/daemon.json ]]; then          # keep container logs from filling the disk
    echo '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}' > /etc/docker/daemon.json
    systemctl restart docker
  fi
  systemctl enable --now docker >/dev/null 2>&1 || true
fi
docker compose version >/dev/null 2>&1 || die "Docker Compose is not available"
if docker ps -a --format '{{.Names}}' | grep -qE '^solis-(influxdb|poller|web)$'; then
  die "containers named solis-* already exist on this machine (another install?). Remove them first."
fi
# Volumes left by an earlier stack would be reused with their old database, which does not accept the new passwords.
project=$(basename "$DIR" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9_-')
old_volumes=$(docker volume ls -q | grep -E "^${project}_(influxdb-data|influxdb-config|web-data)$" || true)
if [[ -n "$old_volumes" ]]; then
  die "Docker volumes from an earlier install exist:
$(echo "$old_volumes" | sed 's/^/  /')
A new install would reuse that old database, which does not accept the new passwords.
To start clean (this DELETES the stored history):  docker volume rm $(echo "$old_volumes" | tr '\n' ' ')
To keep it, restore the .env of that install instead of running the installer."
fi

# ---------------------------------------------------------------- download and pull before touching $DIR
step "Downloading $VERSION"
stage="$(mktemp -d)"
trap 'rc=$?; rm -rf "$stage"; ((rc)) && printf "\nInstall failed (line %s). Nothing is lost: fix the cause and run the same command again.\n" "$LINENO" >&2' EXIT
fetch() { curl -fsSL -m 120 -o "$stage/$2" "$RAW_BASE/$VERSION/$1" || die "could not download $1 of $VERSION"; }
fetch docker-compose.yml docker-compose.yml
fetch .env.example .env.example
fetch deploy/solis-monitor solis-monitor
for svc in poller web; do
  docker pull "${IMAGE_PREFIX}-${svc}:${VERSION}" || die "could not pull ${IMAGE_PREFIX}-${svc}:${VERSION}"
done

# ---------------------------------------------------------------- configuration
step "Writing the configuration"
install -d -m 755 "$DIR"
install -m 644 "$stage/docker-compose.yml" "$DIR/docker-compose.yml"
install -m 755 "$stage/solis-monitor" "$DIR/solis-monitor"
install -m 600 "$stage/.env.example" "$DIR/.env"
set_var() {    # set_var NAME VALUE: replace the first NAME= line of .env, or add it
  if grep -qE "^$1=" "$DIR/.env"; then sed -i "s|^$1=.*|$1=$2|" "$DIR/.env"; else echo "$1=$2" >> "$DIR/.env"; fi
}
set_var SITE_NAME "$SITE"
set_var INVERTER_HOST "$INVERTER"
set_var INVERTER_PORT "$MODBUS_PORT"
set_var TZ "$TZ_NAME"
set_var WEB_PORT "$PORT"
set_var INFLUX_PASSWORD "$(random_hex 16)"
set_var INFLUX_TOKEN "$(random_hex 32)"
set_var WEB_BASIC_AUTH "${USER_NAME}:${PASS}"
set_var WEB_AUTH_SETTINGS_ONLY "$([[ -n "$PROTECT_ALL" ]] || echo true)"
set_var CONTROL_ENABLED false
set_var MCP_TOKEN ""
set_var SOLIS_IMAGE_PREFIX "$IMAGE_PREFIX"
set_var SOLIS_VERSION "$VERSION"
set_var SOLIS_REPO "$REPO"

# ---------------------------------------------------------------- start
step "Starting"
cd "$DIR"
docker compose up -d
if [[ -z "$TEST_MODE" ]]; then
  ln -sf "$DIR/solis-monitor" /usr/local/bin/solis-monitor
  if [[ -n "$AUTO_UPDATES" ]]; then "$DIR/solis-monitor" auto-update on; fi
fi

say "Waiting for the dashboard ..."
healthy=""
for _ in $(seq 1 40); do
  if [[ "$(docker inspect -f '{{.State.Health.Status}}' solis-web 2>/dev/null)" == "healthy" ]]; then healthy=1; break; fi
  sleep 3
done
[[ -n "$healthy" ]] || warn "the dashboard did not report healthy yet; check:  docker compose -f $DIR/docker-compose.yml logs web"

if [[ -n "$INVERTER" ]]; then
  sleep "${SOLIS_SETTLE_S:-15}"
  say "--- poller log (expect 'Polling ${INVERTER}:${MODBUS_PORT}' and no errors) ---"
  docker compose logs --tail 6 poller 2>&1 || true
fi

ip_addr=$(hostname -I 2>/dev/null | awk '{print $1}')
ip_addr="${ip_addr:-localhost}"
trap - EXIT
rm -rf "$stage"
cat <<DONE

Done.
  Dashboard: http://${ip_addr}:${PORT}
  Login (asked when you open Settings): ${USER_NAME} / $([[ -n "$generated" ]] && echo "$PASS   <- generated, note it down" || echo "the password you chose")
  Settings:  $DIR/.env  (edit, then: cd $DIR && docker compose up -d)
$([[ -z "$INVERTER" ]] && echo "  The inverter address is not set: put it in INVERTER_HOST in $DIR/.env and run: cd $DIR && docker compose up -d")
  Useful:    solis-monitor status | logs | update | auto-update on
Changing inverter settings from the dashboard is OFF. Read the README ("Settings") before turning it on.
Do not expose port ${PORT} to the internet; use a VPN such as Tailscale for remote access.
DONE
