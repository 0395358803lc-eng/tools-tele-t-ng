#!/usr/bin/env bash
set -euo pipefail
: "${DATABASE_URL:?DATABASE_URL is required}"
db_url="${DATABASE_URL/postgresql+asyncpg:/postgresql:}"
backup="${1:?usage: restore_database.sh BACKUP.sql.gz}"
test -f "$backup"
gzip -dc "$backup" | psql "$db_url" -v ON_ERROR_STOP=1
echo "Database restore completed"
