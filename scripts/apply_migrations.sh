#!/usr/bin/env bash
# Idempotently apply pending SQL migrations, tracked in schema_migrations.
#
# The cron auto-deploy (deploy/auto-update.sh) does not apply migrations; the
# `migrate` one-shot service runs this on every `docker compose up -d`. Each
# scripts/migrations/*.sql is applied at most once (recorded by filename), in
# order. Migrations are idempotent (IF NOT EXISTS); the only data-mutating ones
# (006/007) are safe cleanups, so even a re-attempt is harmless. Statement
# errors are tolerated (ON_ERROR_STOP=0) so an already-applied DDL on a legacy
# DB doesn't block the rest.
#
# Connection comes from PG* env (PGHOST/PGUSER/PGDATABASE/PGPASSWORD).
set -uo pipefail

MIG_DIR="${MIG_DIR:-/scripts/migrations}"
PSQL=(psql -v ON_ERROR_STOP=0 -X -q)

echo "[migrate] applying pending migrations from ${MIG_DIR}"
"${PSQL[@]}" -c "CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now());"

for f in $(ls "${MIG_DIR}"/*.sql 2>/dev/null | sort); do
    b="$(basename "$f")"
    already="$(psql -X -tAqc "SELECT 1 FROM schema_migrations WHERE filename='${b}'" 2>/dev/null)"
    if [ "${already}" = "1" ]; then
        echo "[migrate] skip ${b} (already applied)"
        continue
    fi
    echo "[migrate] applying ${b}"
    "${PSQL[@]}" -f "$f" || echo "[migrate]   ${b}: some statements errored (tolerated)"
    # Record as applied regardless: ON_ERROR_STOP=0 attempted every statement,
    # and migrations are idempotent — avoids re-running cleanups each deploy.
    "${PSQL[@]}" -c "INSERT INTO schema_migrations(filename) VALUES ('${b}')
                     ON CONFLICT DO NOTHING;"
done

echo "[migrate] migrations complete"
