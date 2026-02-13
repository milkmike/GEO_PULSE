#!/bin/bash
# Automated PostgreSQL backup for cis-thermometer
# Runs via cron: daily at 03:00 UTC

set -uo pipefail

BACKUP_DIR="/opt/cis-thermometer/backups"
DB_CONTAINER="cis-thermometer-db-1"
DB_USER="thermo"
DB_NAME="cis_thermometer"
KEEP_DAILY=7
KEEP_WEEKLY=4
DATE=$(date +%Y-%m-%d_%H%M)
DAY_OF_WEEK=$(date +%u)

mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly"

echo "[$(date)] Starting backup..."

# Create compressed dump
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" --format=custom --compress=6 \
  > "$BACKUP_DIR/daily/cis_thermo_${DATE}.dump"

SIZE=$(du -sh "$BACKUP_DIR/daily/cis_thermo_${DATE}.dump" | cut -f1)
echo "[$(date)] Backup created: cis_thermo_${DATE}.dump ($SIZE)"

# Weekly backup (every Sunday)
if [ "$DAY_OF_WEEK" -eq 7 ]; then
  cp "$BACKUP_DIR/daily/cis_thermo_${DATE}.dump" "$BACKUP_DIR/weekly/"
  echo "[$(date)] Weekly backup copied"
fi

# Cleanup old daily backups (keep last N)
cd "$BACKUP_DIR/daily"
ls -t *.dump 2>/dev/null | tail -n +$((KEEP_DAILY + 1)) | xargs -r rm -v

# Cleanup old weekly backups
cd "$BACKUP_DIR/weekly"
ls -t *.dump 2>/dev/null | tail -n +$((KEEP_WEEKLY + 1)) | xargs -r rm -v

echo "[$(date)] Backup complete. Daily: $(ls $BACKUP_DIR/daily/*.dump 2>/dev/null | wc -l), Weekly: $(ls $BACKUP_DIR/weekly/*.dump 2>/dev/null | wc -l)"
