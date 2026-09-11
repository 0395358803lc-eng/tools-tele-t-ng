#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ACTION="${1:-status}"
RUNTIME_DIR="$ROOT/.server"
PID_FILE="$RUNTIME_DIR/app.pid"
LOG_FILE="$RUNTIME_DIR/app.log"
PG_CONTAINER="${POSTGRES_CONTAINER:-check-telegram-postgres}"

mkdir -p "$RUNTIME_DIR"
chmod 700 "$RUNTIME_DIR"

app_running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

db_running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "$PG_CONTAINER" 2>/dev/null || true)" == "true" ]]
}

start_db() {
  if db_running; then return 0; fi
  if docker inspect "$PG_CONTAINER" >/dev/null 2>&1; then
    docker start "$PG_CONTAINER" >/dev/null
  else
    "$ROOT/scripts/provision_postgres.sh" >/dev/null
  fi
  for _ in $(seq 1 30); do
    docker exec "$PG_CONTAINER" pg_isready >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "PostgreSQL did not become ready." >&2
  return 1
}

has_admin_credential() {
  grep -Eq '^WEB_UI_PASSWORD(_SCRYPT|_HASH)?=.+' .env 2>/dev/null
}

start_app() {
  start_db
  if app_running; then return 0; fi
  if ! has_admin_credential; then
    echo "Missing WEB_UI_PASSWORD/WEB_UI_PASSWORD_SCRYPT/WEB_UI_PASSWORD_HASH in .env" >&2
    return 2
  fi
  nohup python -m telegram_phone_number_checker.main web \
    --host 0.0.0.0 --port 8000 >"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  sleep 2
  if ! app_running; then
    echo "Application failed to stay running. See $LOG_FILE" >&2
    return 1
  fi
}

stop_app() {
  if app_running; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    for _ in $(seq 1 20); do
      app_running || break
      sleep 0.25
    done
  fi
  rm -f "$PID_FILE"
}

status() {
  if db_running; then echo "database=running"; else echo "database=stopped"; fi
  if app_running; then echo "app=running pid=$(cat "$PID_FILE")"; else echo "app=stopped"; fi
  echo "log=$LOG_FILE"
}

case "$ACTION" in
  start) start_app ;;
  stop) stop_app ;;
  restart) stop_app; start_app ;;
  status) status ;;
  *) echo "Usage: $0 {start|stop|restart|status}" >&2; exit 2 ;;
esac

status
