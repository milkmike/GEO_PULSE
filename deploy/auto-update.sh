#!/usr/bin/env bash
# Pull-based auto-deploy for GEO PULSE.
#
# Run on a timer (cron/systemd). When origin/main advances, fast-forward and
# rebuild only what changed. No-op when nothing changed, so it's cheap to run
# every few minutes. This is how changes land in prod without manual SSH:
# commit to main → the server picks it up here.
#
# IMPORTANT — keep the working tree CLEAN for this to work:
#   put server-specific values in .env (gitignored), NOT in tracked files.
#   Available .env knobs: GDELT_PAUSE_SEC, ADMIN_API_KEY, CORS_ORIGINS, LLM_MODELS…
#
# One-time setup:
#   crontab -e   →   */5 * * * * /opt/geopulse/deploy/auto-update.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/geopulse}"
LOG="${DEPLOY_LOG:-/var/log/geopulse-deploy.log}"
cd "$APP_DIR"

log() { echo "$(date -Is) $*" >> "$LOG"; }

# Refuse to run on a dirty tracked tree — would block a clean fast-forward.
# (.env and other gitignored files don't count and are safe to keep.)
if ! git diff --quiet || ! git diff --cached --quiet; then
    log "SKIP: working tree has uncommitted changes to tracked files; not deploying"
    exit 0
fi

BEFORE="$(git rev-parse HEAD)"
git fetch -q origin main
AFTER="$(git rev-parse origin/main)"

[ "$BEFORE" = "$AFTER" ] && exit 0   # already up to date

if ! git merge --ff-only origin/main >/dev/null 2>&1; then
    log "SKIP: local main diverged from origin/main; manual intervention needed"
    exit 0
fi

# Rebuild + restart everything (compose only recreates changed services).
docker compose up -d --build >/dev/null 2>&1
log "DEPLOYED ${BEFORE:0:8} -> ${AFTER:0:8}"
