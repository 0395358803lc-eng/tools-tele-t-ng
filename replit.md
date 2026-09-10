# Multi TG Manager

Private dashboard for managing owned Telegram accounts, profiles, groups, messages, and security actions from one server-hosted workspace.

## Run & Operate

- `pnpm --filter @workspace/multi-tg-manager run dev` — build the frontend and run the FastAPI app
- `pnpm --filter @workspace/multi-tg-manager run build` — build the production frontend bundle
- `pnpm run typecheck` — full typecheck across all packages
- `uv run --project . python -m uvicorn app.main:app` from `artifacts/multi-tg-manager/backend` — run the backend directly
- Required shared environment: `DATABASE_URL` — managed PostgreSQL connection string
- Required secrets for Telegram features: `TG_API_ID`, `TG_API_HASH`, `APP_PASSWORD`, `SESSION_SECRET`

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: FastAPI + Uvicorn
- DB: PostgreSQL + SQLAlchemy async + asyncpg
- Telegram: Telethon
- Frontend: React + Vite + Tailwind

## Where things live

- `artifacts/multi-tg-manager/frontend/` — React dashboard source
- `artifacts/multi-tg-manager/backend/app/` — FastAPI routes, Telegram manager, auth, and data models
- `artifacts/multi-tg-manager/backend/schema.sql` — PostgreSQL schema source
- `artifacts/multi-tg-manager/backend/.env.example` — local configuration reference

## Architecture decisions

- PostgreSQL is required; SQLite files are intentionally excluded from the application.
- The frontend is built into `backend/static/` and served by the same FastAPI process.
- The production deployment uses a VM because Telegram sessions and the background status refresh need a continuously running process.
- Telegram session files and remembered 2FA data stay in the private `sessions/` directory and are ignored by version control.

## Product

Users can unlock the dashboard, add and connect Telegram accounts, edit profiles, inspect groups and security messages, send messages, run bulk actions, manage settings, and export account data.

## User preferences

No additional user preferences recorded.

## Gotchas

- Set the four required secrets before using login or Telegram actions; the app intentionally does not ship with placeholder credentials.
- Keep the managed PostgreSQL schema in sync with `backend/schema.sql` before publishing schema changes.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
