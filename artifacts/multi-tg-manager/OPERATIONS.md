# Multi TG Manager — Operations Runbook

## Production prerequisites

The production environment must provide `DATABASE_URL`, `APP_PASSWORD`, `SESSION_SECRET`, and a dedicated `TWOFA_ENCRYPTION_KEY`. `TG_API_ID` and `TG_API_HASH` are optional bootstrap values; normally the operator logs into the dashboard and saves them under Settings, where the pair is encrypted in PostgreSQL. Keep `COOKIE_SECURE=true` when the public endpoint is HTTPS.

Before exposing a deployment, require both checks:

```bash
curl -fsS http://127.0.0.1:$PORT/api/health
curl -fsS http://127.0.0.1:$PORT/api/readiness
```

`health` means the process is alive. `readiness` means PostgreSQL and dashboard authentication are available. Telegram API configuration is reported separately as `telegram_configured`; this allows a fresh deployment to become ready before the operator enters Telegram credentials in the authenticated Settings screen.

## Deployment sequence

1. Install/sync dependencies.
2. Build `frontend/` into `backend/static/`.
3. Run `ops/migrate.sh`.
4. Start Uvicorn with `run-production.sh`.
5. Wait for `/api/readiness` to return HTTP 200.
6. Log into the dashboard and configure Telegram API credentials if they were not bootstrapped from env.
7. Run the security regression suite and a non-destructive Telegram smoke test.

`ops/migrate.sh` detects an old database without `alembic_version`, stamps the legacy baseline, then upgrades through every newer migration. A fresh database is upgraded directly from revision 0001 to head.

## Backup policy

Run a PostgreSQL backup before schema changes and on a regular schedule:

```bash
ops/backup_database.sh /secure/backup/location
```

PostgreSQL is now the canonical backup for application data, encrypted Telegram session snapshots, Telegram API credentials and remembered 2FA data. The legacy session archive remains optional for emergency compatibility:

```bash
BACKUP_PASSPHRASE='strong-secret' ops/backup_sessions.sh /secure/backup/location
```

Do not store encryption keys or backup passphrases beside database dumps. Test database-only restores periodically on a separate environment.

## Restore procedure

For current releases, restore PostgreSQL and the required encryption environment keys, then start the application. The backend recreates missing Telethon `.session` runtime files from `telegram_session_blobs` before connecting accounts.

```bash
ops/restore_database.sh /secure/backup/location/postgres-....sql.gz
```

A legacy encrypted session archive may still be restored if needed. Runtime session directories should remain mode `700` and files mode `600` on Linux.

## Secret rotation

Changing `SESSION_SECRET` invalidates existing dashboard cookies. `TWOFA_ENCRYPTION_KEY` also protects domain-separated encrypted Telegram session snapshots, remembered 2FA data and Telegram API credentials; rotate it only with an explicit decrypt/re-encrypt procedure. A PostgreSQL dump alone is intentionally insufficient without the matching encryption key. Telegram runtime `.session` files remain credentials and must be revoked if exposed.

## Incident handling

If a Telegram session is revoked, mark it disconnected and re-authenticate rather than classifying it as banned. If Telegram reports account deactivation/ban, retain only the tombstone/history record and stop polling that client. If a bulk task is interrupted or rate-limited, treat `partial` as incomplete and review the per-account result list before retrying.

## Release acceptance

A release is not accepted until Python lint, all backend tests, frontend production build and Alembic revision checks pass. For releases changing Telegram behavior, perform live smoke tests with dedicated test accounts for OTP, OTP+2FA, QR, QR+2FA, restart/reconnect, profile read/write and one safe bulk action.
