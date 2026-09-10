#!/usr/bin/env bash
set -euo pipefail
base="${1:-http://127.0.0.1:${PORT:-18942}}"
health=$(curl -fsS "$base/api/health")
ready_code=$(curl -sS -o /tmp/mtm-ready.json -w '%{http_code}' "$base/api/readiness")
settings_code=$(curl -sS -o /tmp/mtm-tg-settings.json -w '%{http_code}' "$base/api/settings/telegram_api")
[[ "$ready_code" == "200" ]] || { echo "deployment-check=FAIL readiness:$ready_code"; exit 1; }
[[ "$settings_code" == "401" ]] || { echo "deployment-check=FAIL telegram-settings:$settings_code"; exit 1; }
printf '%s\n' "$health" | grep -q '"telegram_configured"' || { echo "deployment-check=FAIL old-health-contract"; exit 1; }
echo "deployment-check=PASS"
