#!/usr/bin/env bash
# One-screen health check of this site, for when you are logged in over Tailscale:
# network, containers, which inverter is configured, and what the poller last said.
# A session opened before setup added this user to the docker group can't reach Docker yet: re-run
# under that group instead of failing (no need to log out and in).
if [[ -z "${NO_DOCKER:-}" && -z "${SOLIS_REEXEC:-}" ]] && ! docker info >/dev/null 2>&1; then
  exec env SOLIS_REEXEC=1 sg docker -c "$(printf '%q ' "$0" "$@")"
fi
cd "$(dirname "$0")/../.."
echo "== $(grep -E '^SITE_NAME=' .env | cut -d= -f2-) on $(hostname), $(uptime -p)"
echo "IP: $(hostname -I | awk '{print $1}')   Tailscale: $(tailscale ip -4 2>/dev/null | head -1) ($(tailscale status 2>/dev/null | grep -c . || true) peers visible)"
echo "Inverter: $(grep -E '^INVERTER_HOST=' .env | cut -d= -f2- | sed 's/^$/(not set: run deploy\/pi\/find-inverter.sh at the site)/')"
echo "Deployed: $(git log -1 --format='%h %s' 2>/dev/null)   last deploy: $(tail -1 deploy/deploy.log 2>/dev/null)"
echo; docker compose ps --format 'table {{.Name}}\t{{.Status}}'
echo; echo "--- last poller log lines ---"; docker compose logs --tail 8 poller 2>&1
echo; df -h / | tail -1 | awk '{print "Disk: " $5 " used of " $2}'
