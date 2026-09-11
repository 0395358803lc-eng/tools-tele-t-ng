# telegram-phone-number-checker

Python tool to check if phone numbers are connected to Telegram accounts,
retrieving the connected username, name, and ID where available.

This is a job-oriented Telegram checker and management web application. The
production Web UI uses PostgreSQL for durable jobs, encrypted Telegram
StringSessions, web sessions, audit data, leases and persisted FloodWait state;
SQLite remains supported for local CLI/test workflows. The engine includes
pause/resume, crash recovery, fencing tokens and multi-account parallel jobs so
long runs can restart without losing checkpoints or allowing stale workers to
write over a successor.

> ⚠️ **Use a fresh, dedicated Telegram account**, not your personal one.
> Automations may get your account blocked. A fresh account from a residential
> IP (rather than a known VPN/datacenter IP) works best.
>
> This project **deliberately does not** try to evade or bypass Telegram's rate
> limits. It always honors `FloodWait` and never rotates accounts/proxies to
> get around limits.

## Table of contents

1. [Requirements](#requirements)
2. [Installation](#installation)
3. [.env configuration](#env-configuration)
4. [Web UI](#web-ui)
5. [Telegram login](#telegram-login)
6. [Check phone numbers](#check-phone-numbers)
7. [Import from CSV / TXT / XLSX](#import-from-csv--txt--xlsx)
8. [Job status](#job-status)
9. [Pause a job](#pause-a-job)
10. [Resume a job](#resume-a-job)
11. [Export results](#export-results)
12. [Database location](#database-location)
13. [Logs](#logs)
14. [Privacy / security](#privacy--security)
15. [Troubleshooting](#troubleshooting)

## Requirements

- Python 3.10+
- A Telegram account with an active phone number
- A Telegram `API_ID` / `API_HASH` from https://my.telegram.org/apps

## Installation

Install from source (this repository):

```bash
git clone <this-repo>
cd telegram-phone-number-checker
pip install -r requirements.txt
pip install -e .
```

Or install the package directly:

```bash
pip install .
```

After installation the `telegram-phone-number-checker` command is on your PATH:

```bash
telegram-phone-number-checker --help
```

For development (tests, linting):

```bash
pip install -r requirements-dev.txt
```

## .env configuration

Create a `.env` file in the project root (never commit it):

```
API_ID=
API_HASH=
PHONE_NUMBER=

# Local CLI/tests only
DATABASE_PATH=data/checker.db

# Production Web UI: set one canonical PostgreSQL URL
DATABASE_URL=
PERSISTENCE_MASTER_KEY=

DEFAULT_PHONE_REGION=VN

MAX_ATTEMPTS=5
BASE_RETRY_DELAY_SECONDS=30
MAX_RETRY_DELAY_SECONDS=3600

MIN_REQUEST_INTERVAL_SECONDS=

AUTO_RESUME=false

WORKER_LEASE_SECONDS=60
LEASE_RENEW_FAILURE_LIMIT=3
LEASE_TAKEOVER_GRACE_SECONDS=0
IN_FLIGHT_RECOVERY_GRACE_SECONDS=60

# Web UI
WEB_UI_USERNAME=admin
WEB_UI_PASSWORD=change-me
# Prefer WEB_UI_PASSWORD_SCRYPT for production deployments.
# WEB_UI_PASSWORD_SCRYPT=
# WEB_UI_PASSWORD_HASH=
WEB_UI_COOKIE_SECURE=true
# WEB_UI_SESSION_MAX_AGE_SECONDS=43200
# WEB_UI_HOST=127.0.0.1
# WEB_UI_PORT=8000
AUDIT_RETENTION_DAYS=90
SECURITY_MESSAGE_RETENTION_DAYS=30

LOG_LEVEL=INFO
```

| Variable | Purpose |
| --- | --- |
| `API_ID` / `API_HASH` | Telegram application credentials from my.telegram.org |
| `PHONE_NUMBER` | The account used to log in, e.g. `+84912345678` |
| `DATABASE_PATH` | SQLite path for local CLI/test workflows (default `data/checker.db`) |
| `DATABASE_URL` | Canonical PostgreSQL connection used by the production Web UI |
| `PERSISTENCE_MASTER_KEY` | Required production encryption key for Telegram sessions and encrypted SQL settings |
| `DEFAULT_PHONE_REGION` | ISO region used to parse numbers without `+` (default `VN`) |
| `MAX_ATTEMPTS` | Max attempts per number before it becomes `PERMANENT_ERROR` |
| `BASE_RETRY_DELAY_SECONDS` | Base exponential-backoff delay for temporary errors |
| `MAX_RETRY_DELAY_SECONDS` | Upper bound for backoff |
| `MIN_REQUEST_INTERVAL_SECONDS` | Optional safety pacing between requests (not a bypass) |
| `WORKER_LEASE_SECONDS` | Seconds a worker's job/account lease stays valid without renewal |
| `LEASE_RENEW_FAILURE_LIMIT` | Consecutive lease-renew failures before the worker fails closed |
| `LEASE_TAKEOVER_GRACE_SECONDS` | Extra wait after expiry before another worker may take over |
| `IN_FLIGHT_RECOVERY_GRACE_SECONDS` | Quarantine before retrying an uncertain in-flight request |
| `AUTO_RESUME` | Resume paused jobs on startup automatically |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

All `.env` values can also be passed as CLI options (the env var is the
default). See `telegram-phone-number-checker check --help`.

The Web UI uses the `WEB_UI_*` variables — see [Web UI](#web-ui) below.

## Web UI

The web UI is a FastAPI backend + React (Vite) frontend that gives you the
whole workflow in a browser: create/import jobs, monitor progress live,
pause/resume/delete, export results, and manage the Telegram login (code + 2FA)
without touching the terminal.

### Build the frontend (once)

```bash
cd web
npm install
npm run build
```

This produces `web/dist`, which the backend serves automatically. To rebuild
the frontend after editing `web/src`, just run `npm run build` again.

### Run

```bash
telegram-phone-number-checker web                 # http://127.0.0.1:8000
telegram-phone-number-checker web --port 9000     # custom port
telegram-phone-number-checker web --reload        # dev auto-reload
```

For a server-local deployment, PostgreSQL runs in Docker and is bound only to
`127.0.0.1`. Configure `DATABASE_URL` and `PERSISTENCE_MASTER_KEY` in `.env`,
then run:

```bash
./scripts/provision_postgres.sh
python scripts/set_admin_password.py
./scripts/server_ctl.sh start
./scripts/server_ctl.sh status
```

`set_admin_password.py` stores only an scrypt hash in `.env`; the plaintext
password is never written to disk. `server_ctl.sh` refuses to start the Web UI
without a configured admin credential. Stop/restart with `server_ctl.sh stop`
or `server_ctl.sh restart`.

Back up the production database with:

```bash
python scripts/database_tool.py backup --file backups/checktelegram.dump
python scripts/database_tool.py verify --file backups/checktelegram.dump
```

Production startup fails fast unless a Web UI credential is configured via
`WEB_UI_PASSWORD_SCRYPT`, `WEB_UI_PASSWORD_HASH`, or `WEB_UI_PASSWORD`.

### Using

- **Dashboard** — quick-create a job from a comma-separated list, see live
  status/progress via SSE (no page refresh), pause/resume/delete jobs.
- **Job detail** — filter by status/search, page through results, export JSON
  or CSV.
- **Import** — drag & drop `.txt`, `.csv` or `.xlsx`; selecting multiple
  accounts globally deduplicates E.164 targets and distributes them round-robin.
- **Tài khoản Telegram** — add multiple accounts, login by OTP/QR, migrate a
  valid legacy `.session` into encrypted SQL storage, and inspect account health.
- **Cấu hình** — read-only view of the active settings (secrets masked).
- **Manager** — profile, security sessions, groups/channels, chats, target tools
  and audit data, all using the canonical SQL-backed Telegram sessions.

Different Telegram accounts can process independent branches in parallel. A
single account is protected by a shared SQL lease so Checker and Manager
operations cannot use the same Telegram session concurrently.

### Development (frontend)

Run the API `--reload` and, in another terminal:

```bash
cd web
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:8000` and hot-reloads the UI.

## Telegram login

The CLI can still use Telethon's local `*.session` workflow for local runs. In
the Web UI, Telegram sessions are stored canonically as encrypted
`StringSession` values in SQL using `PERSISTENCE_MASTER_KEY`; OTP and QR login
both write to that store. A valid legacy `*.session` can be verified and
migrated into SQL without requesting OTP again.

## Check phone numbers

`check` creates a job, checks the numbers, persists progress, and finishes
the job when **every** number has a terminal result.

```bash
# one or more comma-separated numbers
telegram-phone-number-checker check --phone-numbers +84911111111,+84922222222

# override credentials / other settings for this run
telegram-phone-number-checker check \
    --api-id YOUR_API_ID \
    --api-hash YOUR_API_HASH \
    --api-phone-number +84912345678 \
    --phone-numbers +84911111111

# resume an existing job by ID
telegram-phone-number-checker check --job-id <job_id>
```

Possible per-number results:

1. **`FOUND`** — the number is connected to a Telegram account. Username, name,
   ID, and last-online status are stored and exported.
2. **`NOT_DISCOVERABLE`** — no user was returned. This covers both "the number
   has no Telegram account" and "the user restricts discovery by phone number".
   The tool never claims it is definitely "not on Telegram".
3. **`RETRY_REQUIRED`** — Telegram asked us to retry this contact later.
4. **`RATE_LIMITED`** — a `FloodWait` occurred; the tool waits out the cooldown.
5. **`TEMPORARY_ERROR`** — a transient network/RPC error; retried with backoff.
6. **`PERMANENT_ERROR`** — e.g. invalid phone (never sent to Telegram) or
   retries exhausted.

## Import from CSV / TXT / XLSX

Create a job from TXT, CSV or XLSX. The Web UI recognizes `phone`, `number`,
`phone_number` and `số điện thoại` columns (or the first column), normalizes
valid numbers to E.164, removes duplicates, and records invalid numbers as
`PERMANENT_ERROR` without sending them to Telegram. When multiple accounts are
selected in the Web UI, targets are deduplicated globally before round-robin
distribution so the same normalized number cannot appear in two branches.

```bash
telegram-phone-number-checker import numbers.xlsx --job-name "batch-1"
# -> Imported N unique phone numbers into job <job_id>
```

## Job status

```bash
telegram-phone-number-checker job status <job_id>
```

Shows status, totals, found, not-discoverable, retry queue, and error counts.

## Pause a job

Pause is safe: the currently in-flight request may finish, but no new numbers
are picked up. The pause request is persisted in the database, so a running
worker (in any process) honors it.

```bash
telegram-phone-number-checker job pause <job_id>
```

## Resume a job

```bash
telegram-phone-number-checker job resume <job_id>
```

This sets the job back to `RUNNING`. To actually continue working, run a worker
for the job:

```bash
telegram-phone-number-checker check --job-id <job_id>
```

A job is only `COMPLETED` when 100% of its numbers are terminal — it is never
marked complete while a future retry is still pending.

## Export results

```bash
telegram-phone-number-checker export <job_id> results.json --format json
telegram-phone-number-checker export <job_id> results.csv --format csv
```

Exports all numbers with their final status and (for `FOUND`) the captured
Telegram metadata. You can also pass `--output results.json` to `check` to
export automatically after a run.

## Database location

Local CLI/test workflows can use SQLite at `data/checker.db` (override with
`DATABASE_PATH`). The production Web UI requires PostgreSQL by default through
`DATABASE_URL`; it does not silently fall back to a
local SQLite file. Core persisted tables include:

- `jobs` / `check_items` — job metadata, checkpoints, retries and fenced ownership
- `account_runtime_state` / `account_worker_state` — persisted FloodWait and shared account leases
- `telegram_accounts` / `telegram_sessions` — account metadata and encrypted canonical SQL sessions
- `web_sessions` / `telegram_login_sessions` — durable Web UI and Telegram login state
- `manager_audit_logs` / `manager_security_messages` — manager audit/security data

Because every number is its own checkpoint, a crash or restart only re-picks
numbers that were `PENDING` / due for `RETRY_REQUIRED` / `TEMPORARY_ERROR` /
`IN_FLIGHT_UNKNOWN` after its recovery grace. A `PROCESSING` item left by a
dead owner is first quarantined as `IN_FLIGHT_UNKNOWN`; it is never immediately
retried. Completed (`FOUND` / `NOT_DISCOVERABLE` / `PERMANENT_ERROR`) numbers
are never re-checked. Each active claim carries a unique `processing_token`,
which fences late writes from an old worker after takeover.

## Logs

The tool emits structured JSON logs to stdout, e.g.:

```json
{"timestamp":"...","level":"INFO","logger":"...","event":"PHONE_CHECK_STARTED","job_id":"...","item_id":1,"phone":"+8491****5678","attempt":1}
```

Phones are masked in logs and no credentials are ever logged.

## Privacy / security

- **Never** commit `.env`, `*.session`, `*.session-journal`, `*.db`, or
  `logs/` — they are git-ignored.
- Phones are masked (`+8491****5678`) in logs.
- `API_ID`, `API_HASH`, `PHONE_NUMBER`, OTP codes, passwords, session contents,
  and tokens are never written to logs.
- The web UI signs session cookies with `WEB_UI_SECRET_KEY`; set a fixed value
  so sessions survive restarts (otherwise a random key is generated each run).
- The release/release-builder refuses to ship `.env`, `.session`, or `.db`.

## Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| `Enter the code` at startup | First-time login; enter the code sent to your `PHONE_NUMBER`. |
| Login code exhausted / invalid | Re-run; Telegram sends a new code. |
| Job stays `PAUSED` | A previous run was paused. `job resume` then `check --job-id`. |
| `Job ... already has a live worker` | Another process is running it; pause or wait. |
| `FloodWait` / long wait | Telegram rate limit. The tool honors it and resumes automatically. |
| Numbers all `NOT_DISCOVERABLE` | They may have no account or block number-search (cannot be distinguished). |
| Job never reaches `COMPLETED` | A number is still retrying; wait for retries, or `job pause` then inspect. |

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

This project uses [poetry](https://python-poetry.org/) for packaging; the
`pyproject.toml` is the source of truth for dependencies, and
`requirements*.txt` are kept in sync for pip-only workflows.
