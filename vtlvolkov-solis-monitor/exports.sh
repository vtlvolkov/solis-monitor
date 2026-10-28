# shellcheck shell=bash disable=SC2154,SC2155
# (umbrelOS provides derive_entropy and the variables used here; `export X="$(...)"` is Umbrel's own form and none of these can fail)
# Sourced by umbrelOS before the app starts (no shebang, no exit, see the Umbrel packaging notes).
# Secrets are derived from the device's seed, so they are stable across restarts and never stored in this repo.
_solis_derived_password="$(derive_entropy "${app_entropy_identifier}-influx-password")"
_solis_derived_token="$(derive_entropy "${app_entropy_identifier}-influx-token")"

# The few things the owner may change live in one plain file, written by hooks/pre-start on the first start:
#   <umbrel>/app-data/vtlvolkov-solis-monitor/data/config/settings.env     (KEY=value lines; written by hooks/pre-start)
# Restart the app after editing it. Unset or missing keys fall back to the defaults in docker-compose.yml.
# EXPORTS_APP_DATA_DIR is already the app's data root (<umbrel>/app-data/<id>/data), the same folder hooks call APP_DATA_ROOT.
_solis_settings="${EXPORTS_APP_DATA_DIR}/config/settings.env"
_solis_get() {
  [ -f "${_solis_settings}" ] || return 0
  sed -n "s/^${1}=[[:space:]]*//p" "${_solis_settings}" | sed -n '1{s/[[:space:]]*#.*$//;s/[[:space:]]*$//;p;}'
}
# A database that already exists (moved from another install) only accepts the password and token it was created with.
_solis_password="$(_solis_get INFLUX_PASSWORD)"
_solis_token="$(_solis_get INFLUX_TOKEN)"
export APP_VTLVOLKOV_SOLIS_MONITOR_INFLUX_PASSWORD="${_solis_password:-${_solis_derived_password}}"
export APP_VTLVOLKOV_SOLIS_MONITOR_INFLUX_TOKEN="${_solis_token:-${_solis_derived_token}}"
export APP_VTLVOLKOV_SOLIS_MONITOR_INVERTER_HOST="$(_solis_get INVERTER_HOST)"
export APP_VTLVOLKOV_SOLIS_MONITOR_INVERTER_PORT="$(_solis_get INVERTER_PORT)"
export APP_VTLVOLKOV_SOLIS_MONITOR_MODBUS_UNIT="$(_solis_get MODBUS_UNIT)"
export APP_VTLVOLKOV_SOLIS_MONITOR_SITE_NAME="$(_solis_get SITE_NAME)"
export APP_VTLVOLKOV_SOLIS_MONITOR_TZ="$(_solis_get TZ)"
export APP_VTLVOLKOV_SOLIS_MONITOR_INFLUX_RETENTION="$(_solis_get INFLUX_RETENTION)"
export APP_VTLVOLKOV_SOLIS_MONITOR_CONTROL_ENABLED="$(_solis_get CONTROL_ENABLED)"
export APP_VTLVOLKOV_SOLIS_MONITOR_CURRENT_SCALE="$(_solis_get CURRENT_SCALE)"
export APP_VTLVOLKOV_SOLIS_MONITOR_MAX_CHARGE_CURRENT_A="$(_solis_get MAX_CHARGE_CURRENT_A)"
export APP_VTLVOLKOV_SOLIS_MONITOR_MAX_DISCHARGE_CURRENT_A="$(_solis_get MAX_DISCHARGE_CURRENT_A)"
export APP_VTLVOLKOV_SOLIS_MONITOR_BATTERY_CAPACITY_KWH="$(_solis_get BATTERY_CAPACITY_KWH)"
export APP_VTLVOLKOV_SOLIS_MONITOR_TELEGRAM_BOT_TOKEN="$(_solis_get TELEGRAM_BOT_TOKEN)"
export APP_VTLVOLKOV_SOLIS_MONITOR_TELEGRAM_CHAT_ID="$(_solis_get TELEGRAM_CHAT_ID)"
export APP_VTLVOLKOV_SOLIS_MONITOR_ALERT_SOC_LEVELS="$(_solis_get ALERT_SOC_LEVELS)"
export APP_VTLVOLKOV_SOLIS_MONITOR_ALERT_OFFLINE_MIN="$(_solis_get ALERT_OFFLINE_MIN)"
export APP_VTLVOLKOV_SOLIS_MONITOR_MCP_TOKEN="$(_solis_get MCP_TOKEN)"
export APP_VTLVOLKOV_SOLIS_MONITOR_TARIFF_CURRENCY="$(_solis_get TARIFF_CURRENCY)"
export APP_VTLVOLKOV_SOLIS_MONITOR_TARIFF_DAY="$(_solis_get TARIFF_DAY)"
export APP_VTLVOLKOV_SOLIS_MONITOR_TARIFF_NIGHT="$(_solis_get TARIFF_NIGHT)"
export APP_VTLVOLKOV_SOLIS_MONITOR_TARIFF_NIGHT_START="$(_solis_get TARIFF_NIGHT_START)"
export APP_VTLVOLKOV_SOLIS_MONITOR_TARIFF_NIGHT_END="$(_solis_get TARIFF_NIGHT_END)"
