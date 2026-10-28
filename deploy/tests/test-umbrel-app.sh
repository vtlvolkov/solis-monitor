#!/usr/bin/env bash
# Tests the Umbrel package's shell pieces without umbrelOS: exports.sh (sourced under `set -euo pipefail`, as Umbrel does,
# with a stand-in derive_entropy) and hooks/pre-start. The compose file's structure is checked by check-umbrel-app.py.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="$ROOT/vtlvolkov-solis-monitor"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
fails=0
ok() { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fails=$((fails + 1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1  [$2]"; fi; }

# the exported value of one variable, after sourcing exports.sh against the settings in $T/data
exported() {
  bash -c 'set -euo pipefail
           derive_entropy() { printf "entropy(%s)" "$1"; }
           app_entropy_identifier=app-vtlvolkov-solis-monitor
           EXPORTS_APP_DATA_DIR="$1"
           . "$2/exports.sh"
           printf "%s" "${!3-UNSET}"' _ "$T" "$APP" "APP_VTLVOLKOV_SOLIS_MONITOR_$1"
}

echo "exports.sh"
rm -rf "$T/data"; mkdir -p "$T/data/config"
check "secrets come from the device seed" '[[ "$(exported INFLUX_TOKEN)" == "entropy(app-vtlvolkov-solis-monitor-influx-token)" && "$(exported INFLUX_PASSWORD)" == "entropy(app-vtlvolkov-solis-monitor-influx-password)" ]]'
check "no settings file: everything empty, nothing fails" '[[ -z "$(exported INVERTER_HOST)" && "$(exported CONTROL_ENABLED)" == "" ]]'
cat > "$T/data/config/settings.env" <<'SET'
# a comment
INVERTER_HOST=192.168.0.105   # my logger
INVERTER_HOST=10.0.0.9
TZ=Europe/Kyiv
SITE_NAME=
TZX=wrong
CONTROL_ENABLED=false
TELEGRAM_CHAT_ID=  12345  
SET
check "values are read, trimmed and stripped of comments" '[[ "$(exported INVERTER_HOST)" == "192.168.0.105" && "$(exported TZ)" == "Europe/Kyiv" && "$(exported TELEGRAM_CHAT_ID)" == "12345" ]]'
check "the first of repeated keys wins; similar keys do not leak" '[[ "$(exported INVERTER_HOST)" != "10.0.0.9" && "$(exported TZ)" == "Europe/Kyiv" ]]'
check "an empty value stays empty" '[[ -z "$(exported SITE_NAME)" ]]'
check "a value with special characters is passed through unchanged" '
  printf "SITE_NAME=Дача & \"home\" \$HOME\n" > "$T/data/config/settings.env"; [[ "$(exported SITE_NAME)" == "Дача & \"home\" \$HOME" ]]'

check "the database password and token can be overridden, to adopt an existing database" '
  printf "INFLUX_TOKEN=old-token\nINFLUX_PASSWORD=old-password\n" > "$T/data/config/settings.env"
  [[ "$(exported INFLUX_TOKEN)" == old-token && "$(exported INFLUX_PASSWORD)" == old-password ]]'
check "and are derived again when the override is removed" '
  : > "$T/data/config/settings.env"; [[ "$(exported INFLUX_TOKEN)" == "entropy(app-vtlvolkov-solis-monitor-influx-token)" ]]'

echo "hooks/pre-start"
rm -rf "$T/data"
APP_DATA_ROOT="$T/data" bash "$APP/hooks/pre-start" && ok "runs on a fresh install" || bad "runs on a fresh install"
check "creates the settings file" '[[ -s "$T/data/config/settings.env" ]]'
check "the file it writes means: no inverter yet, control off, port 502" '
  [[ -z "$(exported INVERTER_HOST)" && "$(exported INVERTER_PORT)" == 502 && "$(exported CONTROL_ENABLED)" == false && "$(exported TARIFF_NIGHT_START)" == 23:00 ]]'
echo "INVERTER_HOST=192.168.0.105" > "$T/data/config/settings.env"
APP_DATA_ROOT="$T/data" bash "$APP/hooks/pre-start"
check "never overwrites what the owner edited" '[[ "$(cat "$T/data/config/settings.env")" == "INVERTER_HOST=192.168.0.105" ]]'

echo
if ((fails)); then echo "$fails check(s) failed"; exit 1; fi
echo "all checks passed"
