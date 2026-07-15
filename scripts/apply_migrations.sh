#!/usr/bin/env bash
# Idempotently apply pending SQL migrations, tracked in schema_migrations.
#
# The cron auto-deploy (deploy/auto-update.sh) does not apply migrations; the
# `migrate` one-shot service runs this on every `docker compose up -d`. Each
# scripts/migrations/*.sql is applied at most once (recorded by filename), in
# order. Migrations are idempotent (IF NOT EXISTS); the only data-mutating ones
# (006/007) are safe cleanups, so a retry after a failure is harmless. A file is
# recorded only after every statement succeeds.
#
# Connection comes from PG* env (PGHOST/PGUSER/PGDATABASE/PGPASSWORD).
set -euo pipefail
shopt -s nullglob

MIG_DIR="${MIG_DIR:-/scripts/migrations}"
PSQL=(psql -v ON_ERROR_STOP=1 -X -q)

echo "[migrate] applying pending migrations from ${MIG_DIR}"
"${PSQL[@]}" -c "CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now());"

for f in "${MIG_DIR}"/*.sql; do
    b="$(basename "$f")"
    already="$("${PSQL[@]}" -tA -c \
        "SELECT 1 FROM schema_migrations WHERE filename='${b}'")"
    if [ "${already}" = "1" ]; then
        echo "[migrate] skip ${b} (already applied)"
        continue
    fi
    echo "[migrate] applying ${b}"
    "${PSQL[@]}" -f "$f"
    "${PSQL[@]}" -c "INSERT INTO schema_migrations(filename) VALUES ('${b}')
                     ON CONFLICT DO NOTHING;"
done

echo "[migrate] migrations complete"
