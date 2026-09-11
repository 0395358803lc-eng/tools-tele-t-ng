#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
CONTAINER="${POSTGRES_CONTAINER:-check-telegram-postgres}"
VOLUME="${POSTGRES_VOLUME:-check-telegram-postgres-data}"

if [[ ! -f .env ]]; then
  echo ".env not found" >&2
  exit 2
fi

eval "$(python - <<'PY'
from dotenv import dotenv_values
from urllib.parse import urlsplit, unquote
import shlex
url=(dotenv_values('.env').get('DATABASE_URL') or '').strip()
if not url:
    raise SystemExit('DATABASE_URL is missing from .env')
p=urlsplit(url)
if p.scheme not in {'postgres','postgresql'} or p.hostname not in {'127.0.0.1','localhost'}:
    raise SystemExit('DATABASE_URL must point to local PostgreSQL')
values={
 'PG_USER': unquote(p.username or ''),
 'PG_PASS': unquote(p.password or ''),
 'PG_DB': unquote((p.path or '/').lstrip('/')),
 'PG_PORT': str(p.port or 5432),
}
for key,value in values.items(): print(f'{key}={shlex.quote(value)}')
PY
)"

if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  docker start "$CONTAINER" >/dev/null 2>&1 || true
else
  docker volume inspect "$VOLUME" >/dev/null 2>&1 || docker volume create "$VOLUME" >/dev/null
  docker run -d --name "$CONTAINER" --restart unless-stopped \
    -e POSTGRES_DB="$PG_DB" -e POSTGRES_USER="$PG_USER" -e POSTGRES_PASSWORD="$PG_PASS" \
    -p "127.0.0.1:${PG_PORT}:5432" \
    -v "$VOLUME:/var/lib/postgresql/data" postgres:16-alpine >/dev/null
fi

for _ in $(seq 1 60); do
  if docker exec "$CONTAINER" pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
    echo "postgres=ready"
    echo "container=$CONTAINER"
    echo "bind=127.0.0.1:$PG_PORT"
    exit 0
  fi
  sleep 1
done

echo "PostgreSQL did not become ready." >&2
exit 1
