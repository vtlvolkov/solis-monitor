#!/usr/bin/env bash
# Tests for install.sh and deploy/solis-monitor without root, Docker containers or the internet:
# a stand-in `docker` records its calls, a tiny Modbus server plays the data logger, and
# the "release" is a folder of files read through file:// URLs.   Usage: deploy/tests/test-install.sh
# shellcheck disable=SC2034   # variables are read by the eval in check()
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
T="$(mktemp -d)"
trap 'kill "${MOCK_PID:-}" 2>/dev/null; rm -rf "$T"' EXIT
REAL_DOCKER="$(command -v docker || true)"
fails=0
ok() { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fails=$((fails + 1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1  [$2]"; fi; }

# --- a release: the files the installer downloads, under raw/<tag>/
release() {      # release <tag>
  mkdir -p "$T/raw/$1/deploy"
  cp "$ROOT/docker-compose.yml" "$ROOT/.env.example" "$T/raw/$1/"
  cp "$ROOT/deploy/solis-monitor" "$T/raw/$1/deploy/"
}
latest() { mkdir -p "$T/api/releases"; printf '{"tag_name": "%s", "draft": false}' "$1" > "$T/api/releases/latest"; }
release v9.9.9
latest v9.9.9

# --- stand-in docker
mkdir -p "$T/bin"
cat > "$T/bin/docker" <<SHIM
#!/usr/bin/env bash
echo "\$*" >> "$T/docker.log"
case "\$1" in
  compose)
    case "\$2" in
      version) echo "Docker Compose version v2.0.0" ;;
      config) exec "$REAL_DOCKER" "\$@" ;;
      *) : ;;
    esac ;;
  inspect)
    case "\$*" in
      *Health*) [[ -n "\${SHIM_UNHEALTHY:-}" ]] && echo unhealthy || echo healthy ;;
      *Running*) echo true ;;
    esac ;;
  volume) printf '%s\n' "\${SHIM_VOLUMES:-}" ;;
  *) : ;;
esac
exit 0
SHIM
chmod +x "$T/bin/docker"
export PATH="$T/bin:$PATH"

# --- a data logger that answers "read 1 input register" on a free local port
MOCK_PORT=$((20000 + RANDOM % 20000))
python3 -I - "$MOCK_PORT" > /dev/null 2>&1 <<'PY' &
import socket, struct, sys
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen(8)
while True:
    c, _ = s.accept()
    try:
        r = c.recv(64)
        c.sendall(struct.pack(">HHHBBBH", struct.unpack(">H", r[:2])[0], 0, 5, r[6], 4, 2, 0))
    except Exception:
        pass
    c.close()
PY
MOCK_PID=$!
sleep 1

export SOLIS_TEST_MODE=1 SOLIS_RAW_BASE="file://$T/raw" SOLIS_API_BASE="file://$T/api" INVERTER_PORT="$MOCK_PORT"
export SOLIS_SETTLE_S=0 SOLIS_HEALTH_TRIES=2 SOLIS_HEALTH_STEP=0
install() { SOLIS_DIR="$T/inst" bash "$ROOT/install.sh" "$@"; }
env_of() { grep -E "^$1=" "$T/inst/.env" | sed -n 1p | cut -d= -f2-; }

echo "install.sh"
out=$(bash "$ROOT/install.sh" --help); check "--help lists the options" '[[ "$out" == *--dry-run* && "$out" == *--inverter* ]]'
out=$(install --yes --dry-run --inverter 127.0.0.1 2>&1); rc=$?
check "dry run succeeds, changes nothing" '[[ $rc -eq 0 && ! -e "$T/inst" && "$out" == *"Dry run"* && ! -s "$T/docker.log" ]]'
out=$(install --yes --password 'bad pass' 2>&1); rc=$?
check "a password with a space is refused" '[[ $rc -ne 0 && "$out" == *password* && ! -e "$T/inst" ]]'
out=$(install --yes --password short 2>&1); check "a short password is refused" '[[ $? -ne 0 && ! -e "$T/inst" ]]'
out=$(install --yes --inverter 999.1.1.1 2>&1); check "a bad address is refused" '[[ $? -ne 0 && ! -e "$T/inst" ]]'

out=$(SHIM_VOLUMES=$'inst_influxdb-data\ninst_web-data' install --yes --inverter 127.0.0.1 2>&1); rc=$?
check "leftover volumes of an earlier stack stop the install" '[[ $rc -ne 0 && "$out" == *"docker volume rm inst_influxdb-data inst_web-data"* && ! -e "$T/inst" ]]'
out=$(SHIM_VOLUMES=inst_influxdb-data SOLIS_DIR="$T/other" bash "$ROOT/install.sh" --yes --inverter 127.0.0.1 --version v9.9.9 2>&1); rc=$?
check "volumes of another project do not matter" '[[ $rc -eq 0 && -e "$T/other/.env" ]]'
rm -rf "$T/other"

out=$(install --yes --user bob --password 'Secret-pw1' --inverter 127.0.0.1 --site-name Test --port 18088 --version v9.9.9 2>&1); rc=$?
check "install succeeds" '[[ $rc -eq 0 ]] || { echo "$out"; false; }'
check ".env is private" '[[ "$(stat -c %a "$T/inst/.env")" == 600 ]]'
check "login written" '[[ "$(env_of WEB_BASIC_AUTH)" == "bob:Secret-pw1" ]]'
check "settings-only auth by default" '[[ "$(env_of WEB_AUTH_SETTINGS_ONLY)" == true ]]'
check "inverter writes stay off" '[[ "$(env_of CONTROL_ENABLED)" == false ]]'
check "inverter, site, port, release set" '[[ "$(env_of INVERTER_HOST)" == 127.0.0.1 && "$(env_of SITE_NAME)" == Test && "$(env_of WEB_PORT)" == 18088 && "$(env_of SOLIS_VERSION)" == v9.9.9 ]]'
check "fresh random secrets" '[[ "$(env_of INFLUX_TOKEN)" =~ ^[0-9a-f]{64}$ && "$(env_of INFLUX_PASSWORD)" =~ ^[0-9a-f]{32}$ && -z "$(env_of MCP_TOKEN)" ]]'
check "images pulled before starting" 'grep -q "^pull ghcr.io/vtlvolkov/solis-monitor-poller:v9.9.9" "$T/docker.log" && grep -q "^pull ghcr.io/vtlvolkov/solis-monitor-web:v9.9.9" "$T/docker.log" && grep -q "^compose up -d" "$T/docker.log"'
if [[ -n "$REAL_DOCKER" ]] && "$REAL_DOCKER" compose version >/dev/null 2>&1; then
  cfg=$(cd "$T/inst" && "$REAL_DOCKER" compose config 2>&1)
  check "compose resolves the release images" '[[ "$cfg" == *"image: ghcr.io/vtlvolkov/solis-monitor-poller:v9.9.9"* && "$cfg" == *"image: ghcr.io/vtlvolkov/solis-monitor-web:v9.9.9"* ]]'
fi
out=$(install --yes 2>&1); check "a second install is refused" '[[ $? -ne 0 && "$out" == *"already installed"* ]]'

rm -rf "$T/inst"
out=$(install --yes --no-scan --scan-network 127.0.0 2>&1)
check "--no-scan installs without an inverter address" '[[ -z "$(env_of INVERTER_HOST)" && "$out" != *Scanning* ]]'
rm -rf "$T/inst"
out=$(install --yes --scan-network 127.0.0 2>&1)
check "scan finds the logger; a generated password is shown" '[[ "$(env_of INVERTER_HOST)" == 127.0.0.1 && "$(env_of WEB_BASIC_AUTH)" =~ ^admin:[0-9a-f]{16}$ && "$out" == *"generated"* ]]'
check "latest release is looked up when no version is given" '[[ "$(env_of SOLIS_VERSION)" == v9.9.9 ]]'

echo "solis-monitor update"
upd() { SOLIS_DIR="$T/inst" bash "$T/inst/solis-monitor" "$@"; }
out=$(upd update 2>&1); check "up to date: nothing to do" '[[ "$out" == *"Already on v9.9.9"* ]]'
release v9.9.10; latest v9.9.10; : > "$T/docker.log"
out=$(upd update 2>&1); rc=$?
check "update moves to the new release" '[[ $rc -eq 0 && "$(env_of SOLIS_VERSION)" == v9.9.10 ]] || { echo "$out"; false; }'
check "images pulled and restarted" 'grep -q "^compose pull" "$T/docker.log" && grep -q "^compose up -d" "$T/docker.log"'
latest v9.9.9
out=$(upd update 2>&1); check "never downgrades by itself" '[[ "$out" == *"not newer"* && "$(env_of SOLIS_VERSION)" == v9.9.10 ]]'
release v9.9.11; latest v9.9.11
out=$(SHIM_UNHEALTHY=1 upd update 2>&1); rc=$?
check "a release that does not become healthy is rolled back" '[[ $rc -ne 0 && "$(env_of SOLIS_VERSION)" == v9.9.10 && "$out" == *"going back"* ]]'
check "other settings untouched by updates" '[[ "$(env_of CONTROL_ENABLED)" == false && "$(env_of INVERTER_HOST)" == 127.0.0.1 ]]'
out=$(upd update --version v9.9.8 2>&1); check "an unknown release fails cleanly" '[[ $? -ne 0 && "$(env_of SOLIS_VERSION)" == v9.9.10 ]]'

echo "solis-monitor as a normal user"
printf '#!/bin/sh\necho "sudo $*" >> "%s/sudo.log"\n' "$T" > "$T/bin/sudo"; chmod +x "$T/bin/sudo"
chmod 000 "$T/inst/.env"
upd status > /dev/null 2>&1
check "an unreadable .env makes it ask for sudo" 'grep -q "^sudo $T/inst/solis-monitor status" "$T/sudo.log"'
chmod 600 "$T/inst/.env"; : > "$T/sudo.log"
upd version > /dev/null 2>&1
check "with a readable .env it does not" '[[ ! -s "$T/sudo.log" ]]'

echo
if ((fails)); then echo "$fails check(s) failed"; exit 1; fi
echo "all checks passed"
