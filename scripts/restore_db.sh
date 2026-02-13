#!/bin/bash
# Restore PostgreSQL from backup
# Usage: ./restore_db.sh [backup_file]
# If no file specified, uses latest daily backup

set -uo pipefail

BACKUP_DIR="/opt/cis-thermometer/backups"
DB_CONTAINER="cis-thermometer-db-1"
DB_USER="thermo"
DB_NAME="cis_thermometer"

BACKUP_FILE="${1:-}"

if [ -z "$BACKUP_FILE" ]; then
    BACKUP_FILE=$(ls -t "$BACKUP_DIR/daily/"*.dump 2>/dev/null | head -1)
    if [ -z "$BACKUP_FILE" ]; then
        echo "ERROR: No backup files found in $BACKUP_DIR/daily/"
        exit 1
    fi
fi

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: Backup file not found: $BACKUP_FILE"
    exit 1
fi

SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
echo "⚠️  RESTORE from: $BACKUP_FILE ($SIZE)"
echo "⚠️  This will REPLACE all data in $DB_NAME"
echo ""
read -p "Type 'RESTORE' to confirm: " CONFIRM

if [ "$CONFIRM" != "RESTORE" ]; then
    echo "Aborted."
    exit 0
fi

echo "[$(date)] Stopping dependent services..."
cd /opt/cis-thermometer
docker compose stop api collector tg-collector analyzer scheduler vox-collector vox-analyzer vox-engine

echo "[$(date)] Creating pre-restore backup..."
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" --format=custom --compress=6 \
  > "$BACKUP_DIR/daily/pre_restore_$(date +%Y%m%d_%H%M%S).dump" 2>/dev/null || true

echo "[$(date)] Restoring..."
docker exec -i "$DB_CONTAINER" pg_restore -U "$DB_USER" -d "$DB_NAME" --clean --if-exists < "$BACKUP_FILE"

echo "[$(date)] Re-applying safety triggers..."
docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "
CREATE OR REPLACE FUNCTION prevent_mass_delete() RETURNS TRIGGER AS \$body\$
DECLARE del_count INTEGER; allowed TEXT;
BEGIN
    BEGIN allowed := current_setting('app.allow_mass_delete');
    EXCEPTION WHEN OTHERS THEN allowed := 'false'; END;
    IF allowed = 'true' THEN RETURN OLD; END IF;
    SELECT COUNT(*) INTO del_count FROM old_table;
    IF del_count > 100 THEN
        RAISE EXCEPTION 'SAFETY: Mass delete blocked (% rows)', del_count;
    END IF;
    RETURN OLD;
END; \$body\$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS guard_articles_mass_delete ON articles;
CREATE TRIGGER guard_articles_mass_delete AFTER DELETE ON articles
    REFERENCING OLD TABLE AS old_table FOR EACH STATEMENT EXECUTE FUNCTION prevent_mass_delete();
DROP TRIGGER IF EXISTS guard_analysis_mass_delete ON analysis;
CREATE TRIGGER guard_analysis_mass_delete AFTER DELETE ON analysis
    REFERENCING OLD TABLE AS old_table FOR EACH STATEMENT EXECUTE FUNCTION prevent_mass_delete();
"

echo "[$(date)] Restarting services..."
docker compose up -d

echo "[$(date)] ✅ Restore complete!"
docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "SELECT COUNT(*) as articles FROM articles; SELECT COUNT(*) as analysis FROM analysis;"
