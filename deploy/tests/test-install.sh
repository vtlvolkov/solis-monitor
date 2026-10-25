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
      config) if [[ "\$*" == *" -q"* ]]; then [[ -n "\${SHIM_CONFIG_FAIL:-}" ]] && { echo "error: invalid .env" >&2; exit 1; }; exit 0
              else exec "$REAL_DOCKER" "\$@"; fi ;;
      pull) cat "$T/inst/control/update-status.json" >> "$T/trace.log" 2>/dev/null ;;
      *) : ;;
    esac ;;
  inspect)
    case "\$*" in
      *Health*) [[ -n "\${SHIM_UNHEALTHY:-}" ]] && echo unhealthy || echo healthy ;;
      *Running*) echo true ;;
      *Config.Env*) cat "$T/influx.env" 2>/dev/null ;;
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
check "the shared control folder exists" '[[ -d "$T/inst/control" ]]'
check "background update checks default to every 6 hours" '[[ "$(env_of UPDATE_CHECK_HOURS)" == 6 ]]'
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
out=$(install --yes --no-scan --no-update-check --scan-network 127.0.0 2>&1)
check "--no-update-check turns the background checks off" '[[ "$(env_of UPDATE_CHECK_HOURS)" == 0 ]]'
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
check "images pulled and restarted" 'grep -q "^compose pull --quiet poller$" "$T/docker.log" && grep -q "^compose pull --quiet web$" "$T/docker.log" && grep -q "^compose up -d" "$T/docker.log"'
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
check "an unreadable .env makes it ask for sudo, once" 'grep -q "^sudo env SOLIS_ELEVATED=1 .* $T/inst/solis-monitor status" "$T/sudo.log"'
: > "$T/sudo.log"
out=$(SOLIS_ELEVATED=1 upd status 2>&1); rc=$?
check "already elevated and still no .env: stops instead of looping" '[[ $rc -ne 0 && "$out" == *"cannot read"* && ! -s "$T/sudo.log" ]]'
chmod 600 "$T/inst/.env"; : > "$T/sudo.log"
upd version > /dev/null 2>&1
check "with a readable .env it does not" '[[ ! -s "$T/sudo.log" ]]'

echo "solis-monitor auto-update"
printf '#!/bin/sh\nexec "$@"\n' > "$T/bin/sudo"; printf '#!/bin/sh\necho "systemctl $*" >> "%s/systemctl.log"\n' "$T" > "$T/bin/systemctl"; chmod +x "$T/bin/sudo" "$T/bin/systemctl"
mkdir -p "$T/units"
SOLIS_UNIT_DIR="$T/units" upd auto-update on > /dev/null 2>&1
check "the nightly unit runs as the folder's owner, not root" 'grep -q "^User=$(id -un)$" "$T/units/solis-monitor-update.service" && grep -q "^ExecStart=$T/inst/solis-monitor update --auto" "$T/units/solis-monitor-update.service"'
check "the timer is enabled" 'grep -q "enable --now solis-monitor-update.timer" "$T/systemctl.log" && grep -q "^OnCalendar=" "$T/units/solis-monitor-update.timer"'
printf '#!/bin/sh\necho "sudo $*" >> "%s/sudo.log"\n' "$T" > "$T/bin/sudo"      # back to the logging stand-in

echo "updates asked for by the dashboard"
printf '#!/bin/sh\n[ "$1" = -u ] && shift 2\nexec "$@"\n' > "$T/bin/sudo"; chmod +x "$T/bin/sudo"      # a sudo that just runs the command
: > "$T/systemctl.log"; rm -f "$T/units"/*
SOLIS_UNIT_DIR="$T/units" upd dashboard-updates on > /dev/null 2>&1
check "dashboard-updates on: path unit watches the request file" 'grep -q "^PathChanged=$T/inst/control/update-request" "$T/units/solis-monitor-dashboard-update.path"'
check "dashboard-updates on: the service runs as the folder owner and passes --from-dashboard" 'grep -q "^User=$(id -un)$" "$T/units/solis-monitor-dashboard-update.service" && grep -q "^ExecStart=$T/inst/solis-monitor update --from-dashboard$" "$T/units/solis-monitor-dashboard-update.service"'
check "dashboard-updates on: bridge file written and unit enabled" 'grep -q "\"enabled\":true" "$T/inst/control/bridge.json" && grep -q "enable --now solis-monitor-dashboard-update.path" "$T/systemctl.log"'

status_of() { sed -n "s/.*\"$1\":\"\([^\"]*\)\".*/\1/p" "$T/inst/control/update-status.json"; }
: > "$T/docker.log"; rm -f "$T/inst/control/update-status.json"
upd update --from-dashboard > /dev/null 2>&1
check "no request file: nothing happens and nothing is reported" '[[ ! -e "$T/inst/control/update-status.json" ]] && ! grep -q "^compose" "$T/docker.log"'

release v9.9.20; latest v9.9.20
echo '{"to":"v9.9.20"}' > "$T/inst/control/update-request"; : > "$T/trace.log"
upd update --from-dashboard > /dev/null 2>&1; rc=$?
check "a request updates to the newest release and reports ok" '[[ $rc -eq 0 && "$(status_of state)" == ok && "$(status_of from)" == v9.9.10 && "$(status_of to)" == v9.9.20 && "$(env_of SOLIS_VERSION)" == v9.9.20 ]]'
check "progress is reported step by step and ends at 100" '[[ "$(sed -n "s/.*\"percent\":\([0-9]*\).*/\1/p" "$T/inst/control/update-status.json")" == 100 ]] && grep -q "poller image.*\"percent\":20," "$T/trace.log" && grep -q "web image.*\"percent\":45," "$T/trace.log"'
check "the request is consumed and the bridge file shows the new version" '[[ ! -e "$T/inst/control/update-request" ]] && grep -q "v9.9.20" "$T/inst/control/bridge.json"'
check "the status has a start and a finish time" '[[ -n "$(status_of started)" && -n "$(status_of finished)" ]]'

echo '{}' > "$T/inst/control/update-request"
upd update --from-dashboard > /dev/null 2>&1
check "already on the newest: reported as ok, nothing restarted" '[[ "$(status_of state)" == ok && "$(status_of message)" == *"Already"* ]]'

release v9.9.21; latest v9.9.21
echo '{}' > "$T/inst/control/update-request"
SHIM_UNHEALTHY=1 upd update --from-dashboard > /dev/null 2>&1; rc=$?
check "a release that does not start is rolled back and reported" '[[ $rc -ne 0 && "$(status_of state)" == rolled_back && "$(env_of SOLIS_VERSION)" == v9.9.20 ]]'

latest v9.9.22      # announced but not downloadable
echo '{}' > "$T/inst/control/update-request"
upd update --from-dashboard > /dev/null 2>&1; rc=$?
check "a failure before anything changes is still reported (never stuck on running)" '[[ $rc -ne 0 && "$(status_of state)" == failed && "$(env_of SOLIS_VERSION)" == v9.9.20 ]]'

SOLIS_UNIT_DIR="$T/units" upd dashboard-updates off > /dev/null 2>&1
check "dashboard-updates off removes the bridge and disables the unit" '[[ ! -e "$T/inst/control/bridge.json" ]] && grep -q "disable --now solis-monitor-dashboard-update.path" "$T/systemctl.log"'
printf '#!/bin/sh\necho "sudo $*" >> "%s/sudo.log"\n' "$T" > "$T/bin/sudo"      # back to the logging stand-in

echo "solis-monitor apply and edit"
{ for pair in ADMIN_TOKEN:INFLUX_TOKEN PASSWORD:INFLUX_PASSWORD USERNAME:INFLUX_USER ORG:INFLUX_ORG BUCKET:INFLUX_BUCKET; do
    echo "DOCKER_INFLUXDB_INIT_${pair%%:*}=$(env_of "${pair##*:}")"; done; } > "$T/influx.env"      # what the database was created with
: > "$T/docker.log"
out=$(upd apply 2>&1); rc=$?
check "apply restarts what changed and waits for health" '[[ $rc -eq 0 && "$out" == *Applied* ]] && grep -q "^compose up -d --remove-orphans" "$T/docker.log"'
orig_token=$(env_of INFLUX_TOKEN); sed -i "s|^INFLUX_TOKEN=.*|INFLUX_TOKEN=changed|" "$T/inst/.env"; : > "$T/docker.log"
out=$(upd apply 2>&1); rc=$?
check "apply refuses a changed database token" '[[ $rc -ne 0 && "$out" == *INFLUX_TOKEN* ]] && ! grep -q "^compose up" "$T/docker.log"'
out=$(upd apply --force 2>&1); check "--force applies it anyway" 'grep -q "^compose up" "$T/docker.log"'
sed -i "s|^INFLUX_TOKEN=.*|INFLUX_TOKEN=$orig_token|" "$T/inst/.env"; : > "$T/docker.log"
out=$(SHIM_CONFIG_FAIL=1 upd apply 2>&1); rc=$?
check "apply stops on an invalid file" '[[ $rc -ne 0 && "$out" == *"nothing was changed"* ]] && ! grep -q "^compose up" "$T/docker.log"'

printf '#!/bin/sh\necho "FOO=bar" >> "$1"\n' > "$T/ed.sh"; chmod +x "$T/ed.sh"
: > "$T/docker.log"
out=$(SOLIS_EDITOR="$T/ed.sh" upd edit <<< "y" 2>&1); rc=$?
check "edit: change, then apply" '[[ $rc -eq 0 && "$(env_of FOO)" == bar && "$out" == *Applied* ]] && grep -q "^compose up" "$T/docker.log"'
sed -i '/^FOO=/d' "$T/inst/.env"; : > "$T/docker.log"
out=$(SOLIS_EDITOR=true upd edit 2>&1); check "edit: no change, nothing happens" '[[ "$out" == *"No changes"* ]] && ! grep -q "^compose up" "$T/docker.log"'
out=$(SOLIS_EDITOR="$T/ed.sh" upd edit <<< "n" 2>&1)
check "edit: answering no keeps the file for a later apply" '[[ "$(env_of FOO)" == bar && "$out" == *"solis-monitor apply"* ]] && ! grep -q "^compose up" "$T/docker.log"'
sed -i '/^FOO=/d' "$T/inst/.env"
out=$(SHIM_CONFIG_FAIL=1 SOLIS_EDITOR="$T/ed.sh" upd edit <<< "n" 2>&1); rc=$?
check "edit: an invalid file is rolled back" '[[ $rc -ne 0 && -z "$(env_of FOO)" && "$out" == *restored* ]]'

echo
if ((fails)); then echo "$fails check(s) failed"; exit 1; fi
echo "all checks passed"
