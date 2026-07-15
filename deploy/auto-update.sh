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
STATE_FILE="${DEPLOY_STATE_FILE:-${APP_DIR}/.deploy-state/last-successful-commit}"
LOCK_FILE="${DEPLOY_LOCK_FILE:-${APP_DIR}/.deploy-state/auto-update.lock}"

log() { echo "$(date -Is) $*" >> "$LOG"; }

# A deployment can outlive the five-minute cron interval. Keep the lock on an
# open file descriptor for this process's entire lifetime and skip overlaps
# before either git or Docker can mutate state.
mkdir -p "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "SKIP: deployment already running"
    exit 0
fi

cd "$APP_DIR"

# Refuse to run on a dirty tracked tree — would block a clean fast-forward.
# (.env and other gitignored files don't count and are safe to keep.)
if ! git diff --quiet || ! git diff --cached --quiet; then
    log "SKIP: working tree has uncommitted changes to tracked files; not deploying"
    exit 0
fi

LOCAL_HEAD="$(git rev-parse HEAD)"
git fetch -q origin main
AFTER="$(git rev-parse origin/main)"
LAST_DEPLOYED=""
if [ -f "$STATE_FILE" ]; then
    LAST_DEPLOYED="$(tr -d '[:space:]' < "$STATE_FILE")"
fi

# HEAD can already equal origin/main after a previous migration failure. Only
# the durable success marker proves that this commit reached running services.
if [ "$LOCAL_HEAD" = "$AFTER" ] && [ "$LAST_DEPLOYED" = "$AFTER" ]; then
    exit 0
fi

if [ "$LOCAL_HEAD" != "$AFTER" ]; then
    if ! git merge --ff-only origin/main >/dev/null 2>&1; then
        log "SKIP: local main diverged from origin/main; manual intervention needed"
        exit 0
    fi
fi

BEFORE="${LAST_DEPLOYED:-$LOCAL_HEAD}"

# Apply pending DB migrations before swapping containers (idempotent, tracked in
# schema_migrations). The migrate service also runs under `up -d`, but invoking
# it explicitly guarantees the schema is current on every deploy regardless of
# compose restart semantics.
docker compose run --rm migrate >>"$LOG" 2>&1

# Build first at low CPU/IO priority so a heavy image build (Next.js!) cannot
# starve the running containers (the box OOM-froze once, 2026-06-12), then
# swap containers — compose only recreates changed services.
nice -n 19 ionice -c3 docker compose build >/dev/null 2>&1
docker compose up -d >/dev/null 2>&1

# Record success only after migrations, build, and container swap all succeed.
# A same-directory rename makes the marker update atomic across cron retries.
mkdir -p "$(dirname "$STATE_FILE")"
STATE_TMP="${STATE_FILE}.tmp.$$"
printf '%s\n' "$AFTER" > "$STATE_TMP"
mv "$STATE_TMP" "$STATE_FILE"
log "DEPLOYED ${BEFORE:0:8} -> ${AFTER:0:8}"
