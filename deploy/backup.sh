#!/usr/bin/env bash
# Postgres + media zaxira nusxasi. Har kuni feasto-backup.timer orqali ishlaydi.
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/feasto}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/feasto}"
KEEP_DAYS="${KEEP_DAYS:-14}"

set -a; source "$APP_DIR/.env"; set +a

STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

PGPASSWORD="$DB_PASSWORD" pg_dump -h "$DB_HOST" -p "${DB_PORT:-5432}" -U "$DB_USER" -Fc "$DB_NAME" \
  > "$BACKUP_DIR/db-$STAMP.dump"

if [ -d "$APP_DIR/media" ]; then
  tar -czf "$BACKUP_DIR/media-$STAMP.tar.gz" -C "$APP_DIR" media
fi

find "$BACKUP_DIR" -type f -mtime +"$KEEP_DAYS" -delete
echo "Zaxira tayyor: $BACKUP_DIR (db-$STAMP.dump)"
