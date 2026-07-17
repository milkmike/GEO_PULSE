# National Source Wave 1 Release

Run the release gate and retain each audit snapshot. The audit commands are
read-only: they only query the configured wave and protected table counts.

```bash
# Local release gate
.venv/bin/python scripts/validate_source_candidates.py --json
.venv/bin/pytest -q
git status --short
git push origin main

# Production baseline; read-only
ssh geopulse-prod "cd /opt/geopulse && git pull --ff-only"
ssh geopulse-prod "cd /opt/geopulse && docker compose build api collector"
ssh geopulse-prod "cd /opt/geopulse && docker compose run --rm -v /opt/geopulse/backups:/app/backups collector python scripts/audit_source_wave.py --wave 2026-07-17-rss-1 --json --out /app/backups/source-wave1-baseline.json"

# Start only the two newly built services; db and redis are untouched
ssh geopulse-prod "cd /opt/geopulse && docker compose up -d api collector"

# First post-collection snapshot after at least one 30-minute collector cycle
ssh geopulse-prod "cd /opt/geopulse && docker compose run --rm -v /opt/geopulse/backups:/app/backups collector python scripts/audit_source_wave.py --wave 2026-07-17-rss-1 --baseline /app/backups/source-wave1-baseline.json --json --out /app/backups/source-wave1-first.json"

# Twelve-hour snapshot against the same protected-count baseline
ssh geopulse-prod "cd /opt/geopulse && docker compose run --rm -v /opt/geopulse/backups:/app/backups collector python scripts/audit_source_wave.py --wave 2026-07-17-rss-1 --baseline /app/backups/source-wave1-baseline.json --json --out /app/backups/source-wave1-12h.json"

# HTTP smoke checks
ssh geopulse-prod "curl -fsS http://127.0.0.1:8100/api/v2/health/source-coverage"
ssh geopulse-prod "curl -fsS http://127.0.0.1:8100/api/v2/health/sources"
ssh geopulse-prod "curl -fsS http://127.0.0.1:8100/api/v2/countries"
```

## Failed-source rollback

For a failed source, first remove only its new `sources_world.yaml` entry and
deploy `collector`. Then deactivate the exact Wave 1 row with a transaction:

```sql
BEGIN;
UPDATE sources
SET active = FALSE
WHERE id = :source_id
  AND config->>'source_expansion_wave' = '2026-07-17-rss-1'
RETURNING id, name, country_code, active;
COMMIT;
```

Require exactly one returned row. If the update returns zero or multiple rows,
roll back and investigate the ID instead of broadening the predicate. Do not
delete articles collected from the disabled source, historical audit snapshots,
or any other historical data.
