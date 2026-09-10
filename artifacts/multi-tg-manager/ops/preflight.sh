#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root/backend"
uv run --project ../../.. python - <<'PY'
from app.config import settings
missing=[]
for name in ("DATABASE_URL","APP_PASSWORD","SESSION_SECRET","TWOFA_ENCRYPTION_KEY"):
    if not getattr(settings,name,None): missing.append(name)
if missing:
    raise SystemExit("Missing required configuration: " + ", ".join(missing))
if len(settings.APP_PASSWORD) < 8:
    raise SystemExit("APP_PASSWORD must be at least 8 characters")
if len(settings.SESSION_SECRET) < 32:
    raise SystemExit("SESSION_SECRET must be at least 32 characters")
if len(settings.TWOFA_ENCRYPTION_KEY) < 32:
    raise SystemExit("TWOFA_ENCRYPTION_KEY must be at least 32 characters")
try:
    settings.database_url
except RuntimeError as exc:
    raise SystemExit(str(exc)) from exc
if not settings.COOKIE_SECURE:
    print("WARNING: COOKIE_SECURE=false; use true behind production HTTPS")
print("production-preflight=PASS")
PY
