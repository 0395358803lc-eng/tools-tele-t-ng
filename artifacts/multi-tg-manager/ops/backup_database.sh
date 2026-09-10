#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${DATABASE_URL:?DATABASE_URL is required}"
db_url="${DATABASE_URL/postgresql+asyncpg:/postgresql:}"
out_dir="${1:-./backups}"
mkdir -p "$out_dir"
ts=$(date -u +%Y%m%dT%H%M%SZ)
out="$out_dir/postgres-$ts.sql.gz"
tmp="$out.tmp"
trap 'rm -f "$tmp"' EXIT
pg_dump --no-owner --no-privileges "$db_url" | gzip -9 > "$tmp"
mv "$tmp" "$out"
chmod 600 "$out"
echo "$out"
