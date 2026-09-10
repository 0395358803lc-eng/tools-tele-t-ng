#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")" && pwd)
cd "$root"
if [[ -f backend/.env ]]; then
  set -a
  . backend/.env
  set +a
fi
bash ops/start_local_postgres.sh
bash ops/preflight.sh
mkdir -p backups
bash ops/backup_database.sh "$root/backups"
find "$root/backups" -maxdepth 1 -type f -name 'postgres-*.sql.gz' -printf '%T@ %p\n' \
  | sort -nr | tail -n +11 | cut -d' ' -f2- | xargs -r rm -f
bash ops/migrate.sh
cd backend
exec uv run --project ../../.. python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}"
