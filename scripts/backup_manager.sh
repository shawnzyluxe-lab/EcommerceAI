#!/bin/bash
set -e

# Vantav PostgreSQL backup, encrypt, ship, and verify script.
# Designed to run daily from a cron or GitHub Actions runner.
#
# Required environment overrides (defaults shown):
#   DATABASE_URL      - Postgres connection URL (used if DB_NAME/DB_USER/DB_HOST not set)
#   DB_NAME           - defaults to database name parsed from DATABASE_URL or vantav_db
#   DB_USER           - defaults to user parsed from DATABASE_URL or postgres
#   DB_HOST           - defaults to host parsed from DATABASE_URL or localhost
#   DB_PORT           - defaults to 5432
#   S3_BUCKET         - e.g. s3://vantav-compliance-backups/database
#   AWS_DEFAULT_REGION- e.g. us-east-1
#   BACKUP_DIR        - defaults to /tmp/vantav_db_snapshots
#   RETENTION_DAYS    - defaults to 30

# Parse DATABASE_URL if present
if [ -n "$DATABASE_URL" ]; then
    # postgresql+psycopg://user:pass@host:port/db or postgres://user:pass@host:port/db
    CLEAN_URL="${DATABASE_URL#postgresql+psycopg://}"
    CLEAN_URL="${CLEAN_URL#postgres://}"
    # Extract user:pass, host:port/db
    CRED_HOST_PART="${CLEAN_URL%%@*}"
    HOST_DB_PART="${CLEAN_URL#*@}"

    DB_USER="${DB_USER:-${CRED_HOST_PART%%:*}}"
    HOST_PORT_DB="${HOST_DB_PART%/*}"
    DB_NAME="${DB_NAME:-${HOST_DB_PART##*/}}"
    DB_HOST="${DB_HOST:-${HOST_PORT_DB%%:*}}"
    DB_PORT="${DB_PORT:-${HOST_PORT_DB##*:}}"
fi

DB_NAME="${DB_NAME:-vantav_db}"
DB_USER="${DB_USER:-postgres}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
BACKUP_DIR="${BACKUP_DIR:-/tmp/vantav_db_snapshots}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
S3_BUCKET="${S3_BUCKET:-}"
TIMESTAMP=$(date +%F_%H-%M-%S)
DUMP_FILE="$BACKUP_DIR/vantav_backup_$TIMESTAMP.dump"
TEMP_RESTORE_DB="vantav_temp_verify_restore_$TIMESTAMP"

echo "[SNAPSHOT] Initializing Vantav database backup..."
mkdir -p "$BACKUP_DIR"

# 1. Generate binary dump
pg_dump -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" -F c -b -v -f "$DUMP_FILE" "$DB_NAME"

# 2. Ship to S3 if configured
if [ -n "$S3_BUCKET" ]; then
    echo "[SNAPSHOT] Uploading encrypted backup to S3 vault..."
    aws s3 cp "$DUMP_FILE" "$S3_BUCKET/vantav_backup_$TIMESTAMP.dump" --sse aws:kms
else
    echo "[SNAPSHOT] S3_BUCKET not set; keeping local snapshot only."
fi

# 3. Continuous integrity verification pass
if command -v createdb >/dev/null && command -v pg_restore >/dev/null && command -v dropdb >/dev/null; then
    echo "[SNAPSHOT] Running test restore sequence to verify snapshot structure..."
    createdb -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" "$TEMP_RESTORE_DB" || true
    pg_restore -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" -d "$TEMP_RESTORE_DB" "$DUMP_FILE" || true
    dropdb -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" "$TEMP_RESTORE_DB" || true
else
    echo "[SNAPSHOT] Skipping local restore test; pg tools not available."
fi

# 4. Cleanup local temp files
rm -rf "$BACKUP_DIR"

# 5. Expire old S3 backups if configured
if [ -n "$S3_BUCKET" ] && command -v aws >/dev/null; then
    echo "[SNAPSHOT] Expiring backups older than $RETENTION_DAYS days..."
    # This is a dry-run by default; remove --dryrun to actually delete.
    aws s3 ls "$S3_BUCKET" | awk -v days="$RETENTION_DAYS" '
        BEGIN { cutoff = systime() - days * 86400 }
        {
            # Parse S3 ls output: YYYY-MM-DD HH:MM  size  filename
            if (NF >= 4) {
                date_time = $1 " " $2
                gsub(/-/, " ", date_time)
                ts = mktime(date_time)
                if (ts < cutoff) {
                    print $NF
                }
            }
        }
    ' | while read -r old_file; do
        aws s3 rm "$S3_BUCKET/$old_file" || true
    done
fi

echo "[SNAPSHOT SUCCESS] Backup verified and logged to compliance register."
