#!/usr/bin/env bash
# Write a .env for a new site from .env.example, with fresh random secrets.
# Usage: make-env.sh "<site name>" [timezone]      (does nothing if .env already exists)
# Starts view-only (CONTROL_ENABLED=false) with no inverter address yet: find-inverter.sh sets it.
set -euo pipefail
cd "$(dirname "$0")/../.."

site="${1:?usage: make-env.sh \"<site name>\" [timezone]}"
tz="${2:-Europe/Kyiv}"
if [[ -e .env ]]; then echo ".env already exists, leaving it alone"; exit 0; fi

pass=$(openssl rand -hex 8)
set_var() {   # set_var NAME VALUE: replace the first "NAME=..." line, keeping nothing after it
  local name="$1" value="$2"
  grep -qE "^${name}=" .env || { echo "make-env: ${name} not found in .env.example" >&2; exit 1; }
  sed -i "s|^${name}=.*|${name}=${value}|" .env
}

install -m 600 .env.example .env
set_var SITE_NAME "$site"
set_var TZ "$tz"
set_var INVERTER_HOST ""
set_var INFLUX_PASSWORD "$(openssl rand -hex 16)"
set_var INFLUX_TOKEN "$(openssl rand -hex 32)"
set_var WEB_BASIC_AUTH "admin:${pass}"
set_var WEB_AUTH_SETTINGS_ONLY true
set_var CONTROL_ENABLED false
set_var MCP_TOKEN ""
echo "Created .env for \"$site\". Dashboard login (Settings page only): admin / ${pass}"
