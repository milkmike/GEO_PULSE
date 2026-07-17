# National Source Wave 1 Release

Run the release gate and retain each audit snapshot. The audit commands are
read-only: they only query the configured wave and protected table counts.

```bash
# Local release gate
# Resolve the main checkout's virtualenv from either the main checkout or a
# linked worktree, where .venv itself is intentionally not duplicated.
PYTHON="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/.venv/bin/python"
"$PYTHON" scripts/validate_source_candidates.py --json
"$PYTHON" -m pytest -q
git status --short
git push origin main

# Production baseline; read-only
# Run this production section as one shell block. Any failed export, promotion
# preflight, or baseline audit stops the block before services can start.
set -euo pipefail
ssh geopulse-prod "cd /opt/geopulse && git pull --ff-only"
ssh geopulse-prod "cd /opt/geopulse && docker compose build api collector"

# Export the live source identities with one SELECT-only query, then fail closed
# if any Wave 1 publisher overlaps a different production source family.
ssh geopulse-prod "cd /opt/geopulse && mkdir -p backups && docker compose exec -T db psql -U thermo -d cis_thermometer -X -q -t -A -c \"SELECT json_build_object('version',1,'sources',COALESCE((SELECT json_agg(json_build_object('country_code',country_code,'url',url,'config',COALESCE(config,'{}'::jsonb)) ORDER BY id) FROM sources),'[]'::json),'publisher_domains',COALESCE((SELECT json_agg(json_build_object('domain',pd.domain,'country_code',pd.country_code,'url',s.url,'source_expansion_wave',s.config->>'source_expansion_wave') ORDER BY pd.domain) FROM publisher_domains pd JOIN sources s ON s.id=pd.publisher_source_id),'[]'::json));\" > backups/production-source-inventory.json"
ssh geopulse-prod "cd /opt/geopulse && docker compose run --rm --no-deps -v /opt/geopulse/backups:/app/backups:ro collector python scripts/validate_source_candidates.py --promotion-preflight --production-inventory /app/backups/production-source-inventory.json --json > backups/source-promotion-preflight.json"

ssh geopulse-prod "cd /opt/geopulse && docker compose run --rm --no-deps -v /opt/geopulse/backups:/app/backups collector python scripts/audit_source_wave.py --wave 2026-07-17-rss-1 --baseline-only --json --out /app/backups/source-wave1-baseline.json"

# Start only the two newly built services; db and redis are untouched
ssh geopulse-prod "cd /opt/geopulse && docker compose up -d --no-deps api collector"

# First post-collection snapshot after at least one 30-minute collector cycle
ssh geopulse-prod "cd /opt/geopulse && docker compose run --rm --no-deps -v /opt/geopulse/backups:/app/backups collector python scripts/audit_source_wave.py --wave 2026-07-17-rss-1 --baseline /app/backups/source-wave1-baseline.json --json --out /app/backups/source-wave1-first.json"

# Twelve-hour snapshot against the same protected-count baseline
ssh geopulse-prod "cd /opt/geopulse && docker compose run --rm --no-deps -v /opt/geopulse/backups:/app/backups collector python scripts/audit_source_wave.py --wave 2026-07-17-rss-1 --baseline /app/backups/source-wave1-baseline.json --json --out /app/backups/source-wave1-12h.json"

# HTTP smoke checks
ssh geopulse-prod "curl -fsS http://127.0.0.1:8100/api/v2/health/source-coverage"
ssh geopulse-prod "curl -fsS http://127.0.0.1:8100/api/v2/health/sources"
ssh geopulse-prod "curl -fsS http://127.0.0.1:8100/api/v2/countries"
```

## Failed-source rollback

For a failed source, first remove only its new `sources_world.yaml` entry and
deploy `collector`. Then deactivate the exact Wave 1 row. Run the entire block
below as one `psql` script after replacing `123` with the reviewed source ID;
do not paste or execute it one statement at a time:

```sql
\set ON_ERROR_STOP on
\set source_id 123
BEGIN;
UPDATE sources
SET active = FALSE
WHERE id = :'source_id'::integer
  AND config->>'source_expansion_wave' = '2026-07-17-rss-1'
RETURNING id, name, country_code, active;

SELECT :ROW_COUNT::integer = 1 AS exactly_one \gset
\if :exactly_one
  COMMIT;
\else
  ROLLBACK;
  \echo 'Expected exactly one Wave 1 row; transaction rolled back.'
  \quit 1
\endif
```

The `ROW_COUNT` check happens before `COMMIT`; zero or multiple returned rows
take the rollback branch and stop the script. Investigate the ID instead of
broadening the predicate. Do not delete articles collected from the disabled
source, historical audit snapshots, or any other historical data.
