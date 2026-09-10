# Multi TG Manager

Private dashboard for managing Telegram accounts that you own or are authorized to operate. The application uses a React/Vite frontend, FastAPI + Telethon backend, and PostgreSQL for persistent application data.


> Telegram API credentials can be changed at runtime after dashboard login from **Settings → Telegram API Credentials**. The API hash is encrypted before it is stored in PostgreSQL and is never returned to the browser.

## Current architecture

- **Frontend:** `frontend/` — React 18 + Vite + Tailwind.
- **Backend:** `backend/app/` — FastAPI, Telethon account/session manager, auth, security and bulk APIs.
- **Database:** PostgreSQL only. Schema changes are managed by Alembic in `backend/alembic/`.
- **Telegram sessions:** canonical encrypted snapshots are stored in PostgreSQL (`telegram_session_blobs`). Local `.session` files are runtime caches that can be rebuilt from the database.
- **Remembered Telegram 2FA passwords:** encrypted in PostgreSQL (`remembered_twofa`); legacy `twofa.json`/`twofa.enc` files are migrated automatically and removed.
- **Production:** frontend builds into `backend/static/` and is served by the same FastAPI process.

## Required configuration

Set these values in the deployment environment or `backend/.env` for local use:

```env
TG_API_ID=        # optional bootstrap; normally set after login in Settings
TG_API_HASH=      # optional bootstrap; stored encrypted after import
DATABASE_URL=postgresql://user:password@host:5432/database
APP_PASSWORD=
SESSION_SECRET=
COOKIE_SECURE=true
TWOFA_ENCRYPTION_KEY=
```

Use `COOKIE_SECURE=true` behind HTTPS. For local `http://localhost` only, set it to `false`. A dedicated `TWOFA_ENCRYPTION_KEY` is preferred; when omitted the application derives a domain-separated encryption key from `SESSION_SECRET`.

## Run on the server

From the workspace root:

```bash
pnpm --filter @workspace/multi-tg-manager run build
pnpm --filter @workspace/multi-tg-manager run migrate
pnpm --filter @workspace/multi-tg-manager run serve
```

`run-production.sh` performs the migration automatically before starting Uvicorn. `/api/health` is the liveness endpoint. `/api/readiness` returns HTTP 503 until PostgreSQL and app authentication configuration are ready; Telegram configuration is reported separately.

## Tests and quality gates

```bash
cd backend
uv run --project ../../.. ruff check app tests alembic --select E,F,I --ignore E501
uv run --project ../../.. python -m unittest discover -s tests -v
cd ../frontend
npm run build
```

CI runs the same checks. Security regression tests include encoded static path traversal, secure login cookie behavior, app-login rate limiting, encrypted 2FA storage, protected API access, bulk result accounting and QR cancellation.

## Backup and restore

Operational scripts are in `ops/`:

```bash
ops/backup_database.sh
ops/restore_database.sh backups/postgres-....sql.gz
BACKUP_PASSPHRASE='...' ops/backup_sessions.sh
BACKUP_PASSPHRASE='...' ops/restore_sessions.sh backups/sessions-....tar.gz.enc
```

Database backups are permission-restricted and, from migration `0005`, include encrypted Telegram session snapshots and encrypted remembered 2FA data. The separate session archive scripts remain for legacy/emergency compatibility.

## Windows local run

`start.bat` now follows the same PostgreSQL architecture as the server build. It creates the Python environment, installs dependencies, prepares `backend/.env`, builds the frontend, applies Alembic migrations and starts the app at `http://localhost:8000`. PostgreSQL must be available and `DATABASE_URL` must be configured.

## Security notes

Never commit `.env`, `.session`, `twofa.enc`, database dumps or backup passphrases. Treat Telegram session files as credentials. Destructive bulk actions should be used only on accounts and chats you are authorized to manage.
