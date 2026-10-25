#!/usr/bin/env bash
# Find the inverter's data logger on this Pi's network (anything answering on its Modbus TCP port),
# save it as INVERTER_HOST in .env and restart the poller.
# Usage: find-inverter.sh [-y] [a.b.c]    scan a.b.c.1-254 instead of this Pi's own /24
# Run it only AT the second site. At home it would find your home inverter, whose logger allows
# a single client, and the Pi would fight your main poller for it.
set -euo pipefail
# A session opened before setup added this user to the docker group can't reach Docker yet: re-run
# under that group instead of failing (no need to log out and in).
if [[ -z "${NO_DOCKER:-}" && -z "${SOLIS_REEXEC:-}" ]] && ! docker info >/dev/null 2>&1; then
  exec env SOLIS_REEXEC=1 sg docker -c "$(printf '%q ' "$0" "$@")"
fi
cd "$(dirname "$0")/../.."

assume_yes=""
if [[ "${1:-}" == "-y" ]]; then assume_yes=1; shift; fi
if [[ -z "$assume_yes" && -z "${NO_DOCKER:-}" ]]; then
  read -rp "Is this Pi at the SECOND site (not on your home network)? [y/N] " yn
  [[ "$yn" == [yY]* ]] || { echo "Stopped. Run this after the Pi is plugged in at the site."; exit 1; }
fi

PORT="${INVERTER_PORT:-502}"
UNIT="${MODBUS_UNIT:-1}"
# NO_DOCKER=1 is for testing the scan on a machine without the stack.
docker_compose() { [[ -n "${NO_DOCKER:-}" ]] || docker compose "$@"; }

prefix="${1:-}"
if [[ -z "$prefix" ]]; then
  me=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)
  [[ -n "$me" ]] || { echo "No network route. Is the Pi connected to the site's network?" >&2; exit 1; }
  prefix="${me%.*}"
else
  me=""
fi
echo "Scanning ${prefix}.1-254 for Modbus TCP on port ${PORT} ..."

# The logger may allow one client at a time: free it from the poller while we look.
docker_compose stop poller >/dev/null 2>&1 || true
restore() { docker_compose up -d poller >/dev/null 2>&1 || true; }
trap restore EXIT

open_hosts=$(seq 1 254 | xargs -P 100 -I{} sh -c \
  "timeout 1 bash -c 'echo > /dev/tcp/${prefix}.{}/${PORT}' 2>/dev/null && echo ${prefix}.{}" | sort -t. -k4 -n || true)

# Keep only hosts that answer a Modbus "read 1 input register" request. An exception reply
# (e.g. "illegal address") still proves a Modbus server is there.
speaks_modbus() {
  python3 - "$1" "$PORT" "$UNIT" <<'PY'
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
found=()
for h in $open_hosts; do
  [[ "$h" == "$me" ]] && continue
  if speaks_modbus "$h"; then found+=("$h"); else echo "  $h: port open but no Modbus reply, skipped"; fi
done

if ((${#found[@]} == 0)); then
  cat >&2 <<MSG
No Modbus device found on ${prefix}.0/24. Check that:
  - the Pi is on the same network as the inverter's data logger (cable or Wi-Fi),
  - the logger is powered and joined to that network (SolisCloud/app shows it online),
  - nothing else (another monitor) is holding the logger's single connection.
If the logger is on another subnet, run: $0 <a.b.c>   or set INVERTER_HOST in .env by hand.
MSG
  exit 1
fi

if ((${#found[@]} == 1)); then
  host="${found[0]}"
else
  echo "Several Modbus devices answered:"
  for i in "${!found[@]}"; do echo "  $((i + 1))) ${found[$i]}"; done
  read -rp "Which one is the inverter's logger? [1-${#found[@]}] " n
  host="${found[$((n - 1))]:-}"
  [[ -n "$host" ]] || { echo "No such choice" >&2; exit 1; }
fi

echo "Inverter logger: $host"
sed -i "s|^INVERTER_HOST=.*|INVERTER_HOST=${host}|" .env
trap - EXIT
docker_compose up -d poller
if [[ -z "${NO_DOCKER:-}" ]]; then
  sleep 25
  echo "--- poller log (expect 'Polling ${host}:${PORT}' and no errors) ---"
  docker compose logs --tail 15 poller
fi
