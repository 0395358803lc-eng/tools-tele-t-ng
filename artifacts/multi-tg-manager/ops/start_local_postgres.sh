#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
workspace=$(cd "$root/../.." && pwd)
pgdata="${LOCAL_PGDATA:-$workspace/.data/multi-tg-postgres}"
sockdir="${LOCAL_PGSOCK:-$workspace/.data/pgsocket}"
port="${LOCAL_PG_PORT:-55432}"
mkdir -p "$workspace/.data" "$sockdir"
chmod 700 "$sockdir"
if [[ ! -f "$pgdata/PG_VERSION" ]]; then
  initdb -D "$pgdata" --auth=trust --username=runner >/dev/null
  chmod 700 "$pgdata"
fi
grep -q "^listen_addresses = '127.0.0.1'" "$pgdata/postgresql.conf" || echo "listen_addresses = '127.0.0.1'" >> "$pgdata/postgresql.conf"
grep -q "^port = $port" "$pgdata/postgresql.conf" || echo "port = $port" >> "$pgdata/postgresql.conf"
grep -q "^unix_socket_directories = '$sockdir'" "$pgdata/postgresql.conf" || echo "unix_socket_directories = '$sockdir'" >> "$pgdata/postgresql.conf"
if ! pg_ctl -D "$pgdata" status >/dev/null 2>&1; then
  pg_ctl -D "$pgdata" -l "$workspace/.data/multi-tg-postgres.log" start >/dev/null
fi
for _ in {1..10}; do pg_isready -h 127.0.0.1 -p "$port" -U runner >/dev/null 2>&1 && break; sleep 1; done
psql -h 127.0.0.1 -p "$port" -U runner -d postgres -Atqc "select 1 from pg_database where datname='multi_tg_manager'" | grep -q 1 || createdb -h 127.0.0.1 -p "$port" -U runner multi_tg_manager
echo "local-postgres=READY"
