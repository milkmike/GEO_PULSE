#!/usr/bin/env bash
# Idempotently apply pending SQL migrations, tracked in schema_migrations.
#
# The cron auto-deploy invokes the `migrate` one-shot service before rebuilding;
# compose also requires that service on `docker compose up -d`. Each migration
# is applied at most once (recorded by filename), in order, and a file is
# recorded only after every statement succeeds.
#
# Connection comes from PG* env (PGHOST/PGUSER/PGDATABASE/PGPASSWORD).
set -euo pipefail
shopt -s nullglob

MIG_DIR="${MIG_DIR:-/scripts/migrations}"
PSQL=(psql -v ON_ERROR_STOP=1 -X -q)

echo "[migrate] applying pending migrations from ${MIG_DIR}"
"${PSQL[@]}" -c "CREATE TABLE IF NOT EXISTS public.schema_migrations (
    filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now());"

for f in "${MIG_DIR}"/*.sql; do
    b="$(basename "$f")"
    already="$("${PSQL[@]}" -tA -c \
        "SELECT 1 FROM public.schema_migrations WHERE filename='${b}'")"
    if [ "${already}" = "1" ]; then
        echo "[migrate] skip ${b} (already applied)"
        continue
    fi
    echo "[migrate] applying ${b}"
    # Apply the file and record it atomically.  A failed statement rolls back
    # the whole migration instead of leaving an untracked partial schema.
    "${PSQL[@]}" -1 -f "$f" -c \
        "INSERT INTO public.schema_migrations(filename) VALUES ('${b}')
         ON CONFLICT DO NOTHING;"
done

echo "[migrate] migrations complete"
