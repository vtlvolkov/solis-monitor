#!/usr/bin/env bash
# Pull the deploy branch and redeploy when it has new commits. Run on the Umbrel host
# from the systemd timer (or cron) in this folder; safe to run by hand, add --force to
# rebuild without a new commit.
#
# The Docker builds run the test suites, so a commit with failing tests never replaces
# the running containers: compose stops at the build and the old version keeps running.
set -euo pipefail

cd "$(dirname "$0")/.."
BRANCH="${DEPLOY_BRANCH:-main}"
LOG="$PWD/deploy/deploy.log"

exec 9>/tmp/solis-deploy.lock
flock -n 9 || exit 0                      # a previous run is still building

log() { echo "$(date -Is) $*" | tee -a "$LOG"; }

# Telegram notice using TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from .env (silently skipped if unset).
env_value() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | sed 's/[[:space:]]#.*//; s/[[:space:]]*$//'; }
notify() {
  local token chat
  token=$(env_value TELEGRAM_BOT_TOKEN); chat=$(env_value TELEGRAM_CHAT_ID)
  [[ -n "$token" && -n "$chat" ]] || return 0
  local site; site=$(env_value SITE_NAME)
  curl -s -m 15 -o /dev/null "https://api.telegram.org/bot${token}/sendMessage" \
       --data-urlencode "chat_id=${chat}" --data-urlencode "text=${site:+[$site] }$1" || true
}

git fetch --quiet origin "$BRANCH"
local_rev=$(git rev-parse HEAD)
remote_rev=$(git rev-parse "origin/$BRANCH")
if [[ "$local_rev" == "$remote_rev" && "${1:-}" != "--force" ]]; then
  exit 0
fi

log "deploying ${remote_rev:0:7} (running ${local_rev:0:7})"
if ! git merge --ff-only --quiet "origin/$BRANCH"; then
  log "FAILED: origin/$BRANCH is not a fast-forward of the checkout (local edits on the server?)"
  notify "❌ Solis deploy stopped: the server checkout has local changes, so ${remote_rev:0:7} can't be pulled."
  exit 1
fi

# Output goes to the terminal / systemd journal and to the log; pipefail keeps compose's exit code.
if docker compose up -d --build --remove-orphans 2>&1 | tee -a "$LOG"; then
  echo "$remote_rev" > deploy/last-ok
  log "OK ${remote_rev:0:7}"
  notify "🚀 Solis dashboard updated to ${remote_rev:0:7}: $(git log -1 --format=%s "$remote_rev")"
else
  log "FAILED: build or start of ${remote_rev:0:7} failed; the output above says why. Previous containers keep running."
  notify "❌ Solis deploy of ${remote_rev:0:7} failed (build or tests). Still running the previous version; see deploy/deploy.log."
  exit 1
fi
