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
      exec) case "\$*" in                      # the database's CLI, for secure-tokens, backup and restore
              *"influx bucket list"*) echo '[{"id": "0a1b2c3d4e5f6071", "name": "solis"}]' ;;
              *"influx auth create"*) echo '{"id": "7", "description": "x", "token": "READ-ONLY-TOKEN-'"\${SHIM_TOKEN_N:-1}"'", "permissions": ["read:orgs/1/buckets/0a1b2c3d4e5f6071"]}' ;;
              *"influx backup"*) tar -C "$T/fake/influx" -c . ;;                                  # a backup streams out as tar
              *"influx restore"*) cat > "$T/restored-influx.tar" ;;                               # a restore streams one in
              *"poller tar -C /data -c"*) tar -C "$T/fake/poller" -c . ;;
              *"web tar -C /data -c"*) tar -C "$T/fake/web" -c . ;;
              *"poller tar -C /data -x"*) mkdir -p "$T/restored/poller" && tar -C "$T/restored/poller" -x ;;
              *"web tar -C /data -x"*) mkdir -p "$T/restored/web" && tar -C "$T/restored/web" -x ;;
            esac ;;
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
check "the shared control and backups folders exist" '[[ -d "$T/inst/control" && -d "$T/inst/backups" ]]'
check "background update checks default to every 6 hours" '[[ "$(env_of UPDATE_CHECK_HOURS)" == 6 ]]'
check ".env is private" '[[ "$(stat -c %a "$T/inst/.env")" == 600 ]]'
check "login written" '[[ "$(env_of WEB_BASIC_AUTH)" == "bob:Secret-pw1" ]]'
check "settings-only auth by default" '[[ "$(env_of WEB_AUTH_SETTINGS_ONLY)" == true ]]'
check "inverter writes stay off" '[[ "$(env_of CONTROL_ENABLED)" == false ]]'
check "inverter, site, port, release set" '[[ "$(env_of INVERTER_HOST)" == 127.0.0.1 && "$(env_of SITE_NAME)" == Test && "$(env_of WEB_PORT)" == 18088 && "$(env_of SOLIS_VERSION)" == v9.9.9 ]]'
check "fresh random secrets" '[[ "$(env_of INFLUX_TOKEN)" =~ ^[0-9a-f]{64}$ && "$(env_of INFLUX_PASSWORD)" =~ ^[0-9a-f]{32}$ && -z "$(env_of MCP_TOKEN)" ]]'
check "the dashboard gets its own poller secret, not the database token" '[[ "$(env_of CONTROL_TOKEN)" =~ ^[0-9a-f]{64}$ && "$(env_of CONTROL_TOKEN)" != "$(env_of INFLUX_TOKEN)" ]]'
check "a read-only database token is made for the dashboard and applied" '[[ "$(env_of INFLUX_READ_TOKEN)" == READ-ONLY-TOKEN-1 ]] && grep -q "^compose exec -T influxdb influx auth create --org home --read-bucket 0a1b2c3d4e5f6071 " "$T/docker.log" && grep -q "^compose up -d poller web$" "$T/docker.log"'
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

echo "solis-monitor status: which address"
out=$(upd status 2>&1); check "without a saved address it shows the one from .env" '[[ "$out" == *"Inverter:  127.0.0.1"* && "$out" != *"saved on the dashboard"* ]]'
cp "$T/bin/docker" "$T/bin/docker.real"
cat > "$T/bin/docker" <<SHIM2
#!/bin/sh
if [ "\$1 \$2" = "compose exec" ]; then echo '{"host": "192.168.7.7", "port": 502, "unit": 1}'; exit 0; fi
exec "$T/bin/docker.real" "\$@"
SHIM2
chmod +x "$T/bin/docker"
out=$(upd status 2>&1); check "with a saved address it shows that one, and says where .env differs" '[[ "$out" == *"Inverter:  192.168.7.7  (saved on the dashboard; .env has 127.0.0.1)"* ]]'
mv "$T/bin/docker.real" "$T/bin/docker"

echo "solis-monitor secure-tokens"
: > "$T/docker.log"; out=$(upd secure-tokens 2>&1); rc=$?
check "nothing to do when both tokens exist" '[[ $rc -eq 0 && "$out" == *"Already done"* ]] && ! grep -q "^compose up" "$T/docker.log"'
sed -i '/^CONTROL_TOKEN=/d; /^INFLUX_READ_TOKEN=/d' "$T/inst/.env"            # an install from before these existed
: > "$T/docker.log"; out=$(SHIM_TOKEN_N=2 upd secure-tokens 2>&1); rc=$?
check "an older install gets both tokens and a restart of poller and web" '[[ $rc -eq 0 && "$(env_of CONTROL_TOKEN)" =~ ^[0-9a-f]{64}$ && "$(env_of INFLUX_READ_TOKEN)" == READ-ONLY-TOKEN-2 ]] && grep -q "^compose up -d poller web$" "$T/docker.log"'
check ".env stays private" '[[ "$(stat -c %a "$T/inst/.env")" == 600 ]]'
out=$(upd status 2>&1); check "status says the dashboard has a read-only token" '[[ "$out" == *"read-only token"* ]]'
sed -i '/^INFLUX_READ_TOKEN=/d' "$T/inst/.env"
out=$(upd status 2>&1); check "status points to secure-tokens while the admin token is in use" '[[ "$out" == *"solis-monitor secure-tokens"* ]]'
upd secure-tokens >/dev/null 2>&1

echo "solis-monitor backup and restore"
mkdir -p "$T/fake/influx/20261112" "$T/fake/poller" "$T/fake/web"
echo manifest > "$T/fake/influx/20261112/manifest"; echo '{"host": "192.168.7.7"}' > "$T/fake/poller/inverter.json"; echo '{"v": 1}' > "$T/fake/web/tariff.json"
: > "$T/docker.log"; out=$(upd backup 2>&1); rc=$?
bk="$(ls "$T"/inst/backups/solis-monitor-*.tar.gz 2>/dev/null | sed -n 1p)"
check "backup writes one private archive" '[[ $rc -eq 0 && -s "$bk" && "$(stat -c %a "$bk")" == 600 && "$out" == *"Backup written"* ]]'
mkdir -p "$T/unpacked" && tar -xzf "$bk" -C "$T/unpacked"
check "it holds the database, the saved files, .env and a manifest" '[[ -s "$T/unpacked/influxdb.tar" && -s "$T/unpacked/poller-data.tar" && -s "$T/unpacked/web-data.tar" && -s "$T/unpacked/env" && "$(cat "$T/unpacked/manifest.json")" == *"\"version\":\"v9.9.10\""* ]] && tar -tf "$T/unpacked/influxdb.tar" | grep -q 20261112/manifest && grep -q "^WEB_BASIC_AUTH=" "$T/unpacked/env"'
check "the database was backed up online, inside its container" 'grep -q "^compose exec -T influxdb sh -c .*influx backup" "$T/docker.log"'
out=$(upd backup "$T/elsewhere/site.tar.gz" 2>&1); check "a file name of your own is used as given" '[[ -s "$T/elsewhere/site.tar.gz" ]]'
out=$(upd status 2>&1); check "status shows the latest backup" '[[ "$out" == *"Backup:    20"* && "$out" == *"/backups/solis-monitor-"* ]]'
# restore onto an install with other settings and a newer version: the backup's settings come back, the version stays
set_env_test() { if grep -qE "^$1=" "$T/inst/.env"; then sed -i "s|^$1=.*|$1=$2|" "$T/inst/.env"; else echo "$1=$2" >> "$T/inst/.env"; fi; }
set_env_test WEB_BASIC_AUTH "other:Other-pw1"; set_env_test SOLIS_VERSION v9.9.30
: > "$T/docker.log"; rm -rf "$T/restored"
out=$(upd restore "$bk" 2>&1 </dev/null); rc=$?
check "restore asks first, and does nothing without a yes" '[[ $rc -ne 0 && "$out" == *"Nothing changed"* ]] && ! grep -q "influx restore" "$T/docker.log" && [[ "$(env_of WEB_BASIC_AUTH)" == "other:Other-pw1" ]]'
out=$(upd restore "$bk" --yes 2>&1); rc=$?
check "restore --yes puts the database back in full" '[[ $rc -eq 0 ]] && grep -q "^compose stop poller web$" "$T/docker.log" && grep -q "influx restore --full" "$T/docker.log" && tar -tf "$T/restored-influx.tar" | grep -q 20261112/manifest'
check "the saved files are back in both containers, which are then restarted" '[[ "$(cat "$T/restored/poller/inverter.json")" == *192.168.7.7* && -s "$T/restored/web/tariff.json" ]] && grep -q "^compose restart poller web$" "$T/docker.log"'
check ".env is the backup's, except the installed version" '[[ "$(env_of WEB_BASIC_AUTH)" == "$(grep "^WEB_BASIC_AUTH=" "$T/unpacked/env" | cut -d= -f2-)" && "$(env_of WEB_BASIC_AUTH)" != "other:Other-pw1" && "$(env_of SOLIS_VERSION)" == v9.9.30 && "$(stat -c %a "$T/inst/.env")" == 600 ]] && grep -q "^compose up -d --force-recreate" "$T/docker.log"'
check "the database CLI is pointed at the restored token" 'grep -q "^compose exec -T influxdb influx config set --config-name default --token $(env_of INFLUX_TOKEN)$" "$T/docker.log"'
echo "not a backup" > "$T/bogus.tar.gz"
out=$(upd restore "$T/bogus.tar.gz" --yes 2>&1); check "a file that is not a backup is refused before anything is touched" '[[ $? -ne 0 && "$out" == *"not a backup"* ]]'
set_env_test SOLIS_VERSION v9.9.10          # back to what the update tests below expect

echo "solis-monitor reset-login"
: > "$T/docker.log"; out=$(upd reset-login 2>&1)
check "reset-login removes the saved password inside the web container" 'grep -q "^compose exec -T web rm -f /data/auth.json$" "$T/docker.log" && [[ "$out" == *"original login applies again"* ]]'

echo "solis-monitor logs"
: > "$T/docker.log"; upd logs poller > /dev/null 2>&1
check "logs shows recent lines and exits (no follow)" 'grep -q "^compose logs --tail 100 poller$" "$T/docker.log"'
: > "$T/docker.log"; upd logs -f web > /dev/null 2>&1
check "logs -f follows" 'grep -q "^compose logs -f --tail 100 web$" "$T/docker.log"'

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
check "dashboard-updates on: bridge file written and both units enabled" 'grep -q "\"enabled\":true" "$T/inst/control/bridge.json" && grep -q "enable --now solis-monitor-dashboard-update.path solis-monitor-dashboard-backup.path" "$T/systemctl.log"'
check "dashboard-updates on: the backup path unit watches its request file" 'grep -q "^PathChanged=$T/inst/control/backup-request" "$T/units/solis-monitor-dashboard-backup.path" && grep -q "^ExecStart=$T/inst/solis-monitor backup --from-dashboard$" "$T/units/solis-monitor-dashboard-backup.service"'

echo "backups asked for by the dashboard"
bstatus_of() { sed -n "s/.*\"$1\":\"\([^\"]*\)\".*/\1/p" "$T/inst/control/backup-status.json"; }
rm -f "$T/inst/control/backup-status.json"; : > "$T/docker.log"
upd backup --from-dashboard >/dev/null 2>&1
check "no request file: nothing happens and nothing is reported" '[[ ! -e "$T/inst/control/backup-status.json" ]] && ! grep -q "influx backup" "$T/docker.log"'
echo '{"requested_at":"now"}' > "$T/inst/control/backup-request"
out=$(upd backup --from-dashboard 2>&1); rc=$?
check "a request makes an archive in backups/ and reports ok with its name" '[[ $rc -eq 0 && "$(bstatus_of state)" == ok && "$(bstatus_of file)" == solis-monitor-*.tar.gz && -s "$T/inst/backups/$(bstatus_of file)" && ! -e "$T/inst/control/backup-request" ]]'
check "the status has a start and a finish time" '[[ -n "$(bstatus_of started)" && -n "$(bstatus_of finished)" ]]'
for i in 1 2 3 4 5 6; do : > "$T/inst/backups/solis-monitor-2026010$i-000000.tar.gz"; touch -d "2026-01-0$i" "$T/inst/backups/solis-monitor-2026010$i-000000.tar.gz"; done
echo '{}' > "$T/inst/control/backup-request"; upd backup --from-dashboard >/dev/null 2>&1
check "the dashboard's backups folder keeps the newest 5" '[[ "$(ls "$T"/inst/backups/solis-monitor-*.tar.gz | wc -l)" == 5 && ! -e "$T/inst/backups/solis-monitor-20260101-000000.tar.gz" && ! -e "$T/inst/backups/solis-monitor-20260102-000000.tar.gz" ]]'
echo '{}' > "$T/inst/control/backup-request"; mv "$T/bin/docker" "$T/bin/docker.off"; printf '#!/bin/sh\nexit 1\n' > "$T/bin/docker"; chmod +x "$T/bin/docker"
out=$(upd backup --from-dashboard 2>&1); rc=$?
mv "$T/bin/docker.off" "$T/bin/docker"
check "a failure is reported, never left as running" '[[ $rc -ne 0 && "$(bstatus_of state)" == failed ]]'
check "the dashboard gets an index of the backup folder" '[[ "$(sed -n "s/.*\"dir\":\"\([^\"]*\)\".*/\1/p" "$T/inst/control/backups.json")" == "$T/inst/backups" ]] && grep -q "\"name\":\"solis-monitor-20260106-000000.tar.gz\",\"size\":0" "$T/inst/control/backups.json"'

echo "backup folder set from the dashboard"
mkdir -p "$T/mnt/usb/solis" "$T/mnt/ro"; chmod 500 "$T/mnt/ro"
set_env_test SOLIS_BACKUP_ROOTS "$T/mnt:/nonexistent/other"     # the test folders are not under /mnt or /media themselves
cfg_of() { sed -n "s/.*\"$1\":\"\?\([^\",}]*\)\"\?.*/\1/p" "$T/inst/control/backup-config.json"; }
echo '{"action":"set-folder","dir":"/etc","keep":5}' > "$T/inst/control/backup-request"
out=$(upd backup --from-dashboard 2>&1); check "a folder outside the allowed places is refused" '[[ $? -ne 0 && "$(bstatus_of state)" == failed && "$(bstatus_of message)" == *"not allowed"* && "$(cfg_of dir)" == "$T/inst/backups" ]]'
echo "{\"action\":\"set-folder\",\"dir\":\"$T/mnt/nowhere\",\"keep\":5}" > "$T/inst/control/backup-request"
out=$(upd backup --from-dashboard 2>&1); check "a folder that does not exist is refused" '[[ "$(bstatus_of state)" == failed && "$(bstatus_of message)" == *"does not exist"* ]]'
echo "{\"action\":\"set-folder\",\"dir\":\"$T/mnt/ro\",\"keep\":5}" > "$T/inst/control/backup-request"
out=$(upd backup --from-dashboard 2>&1); check "a folder that cannot be written is refused" '[[ "$(bstatus_of state)" == failed && "$(bstatus_of message)" == *"not writable"* ]] || [[ $EUID -eq 0 ]]'
echo "{\"action\":\"set-folder\",\"dir\":\"$T/mnt/usb/solis\",\"keep\":3}" > "$T/inst/control/backup-request"
out=$(upd backup --from-dashboard 2>&1); rc=$?
check "an existing folder under an allowed root is accepted and saved" '[[ $rc -eq 0 && "$(bstatus_of state)" == ok && "$(bstatus_of message)" == "Folder set to $T/mnt/usb/solis" && "$(cfg_of dir)" == "$T/mnt/usb/solis" && "$(cfg_of keep)" == 3 ]]'
echo '{}' > "$T/inst/control/backup-request"; upd backup --from-dashboard >/dev/null 2>&1
check "backups then go to that folder, with the index and its own keep count" '[[ "$(ls "$T"/mnt/usb/solis/solis-monitor-*.tar.gz | wc -l)" == 1 && "$(sed -n "s/.*\"dir\":\"\([^\"]*\)\".*/\1/p" "$T/inst/control/backups.json")" == "$T/mnt/usb/solis" ]]'
for i in 1 2 3; do echo '{}' > "$T/inst/control/backup-request"; sleep 1; upd backup --from-dashboard >/dev/null 2>&1; done
check "the folder keeps only the newest 3 asked for from the dashboard" '[[ "$(ls "$T"/mnt/usb/solis/solis-monitor-*.tar.gz | wc -l)" == 3 ]]'
sleep 1; out=$(upd backup 2>&1); check "a backup by hand also goes to the set folder" '[[ "$out" == *"Backup written: $T/mnt/usb/solis/"* && "$(ls "$T"/mnt/usb/solis/solis-monitor-*.tar.gz | wc -l)" == 4 ]]'
out=$(upd status 2>&1); check "status shows the latest backup in the set folder" '[[ "$out" == *"Backup:    20"* && "$out" == *"$T/mnt/usb/solis/"* ]]'

echo "backup removed from the dashboard"
victim="$(ls -1t "$T"/mnt/usb/solis/solis-monitor-*.tar.gz | sed -n 2p | xargs -n1 basename)"
echo "{\"action\":\"remove\",\"name\":\"$victim\"}" > "$T/inst/control/backup-request"
out=$(upd backup --from-dashboard 2>&1); rc=$?
check "a listed archive is removed, the index follows, and the page is told" '[[ $rc -eq 0 && ! -e "$T/mnt/usb/solis/$victim" && "$(ls "$T"/mnt/usb/solis/solis-monitor-*.tar.gz | wc -l)" == 3 && "$(bstatus_of state)" == ok && "$(bstatus_of message)" == "Removed $victim" ]] && ! grep -q "$victim" "$T/inst/control/backups.json"'
: > "$T/inst/.env.keep"; echo '{"action":"remove","name":"../../.env.keep"}' > "$T/inst/control/backup-request"
out=$(upd backup --from-dashboard 2>&1); check "a name that is not an archive is refused" '[[ $? -ne 0 && -e "$T/inst/.env.keep" && "$(bstatus_of state)" == failed && "$(bstatus_of message)" == *"not the name of a backup"* ]]'
echo '{"action":"remove","name":"solis-monitor-19990101-000000.tar.gz"}' > "$T/inst/control/backup-request"
out=$(upd backup --from-dashboard 2>&1); check "a missing archive is reported" '[[ $? -ne 0 && "$(bstatus_of message)" == "No such backup." ]]'

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
check "dashboard-updates off removes the bridge and disables both units" '[[ ! -e "$T/inst/control/bridge.json" ]] && grep -q "disable --now solis-monitor-dashboard-update.path solis-monitor-dashboard-backup.path" "$T/systemctl.log"'
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
