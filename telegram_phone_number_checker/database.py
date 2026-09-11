import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - only relevant before dependencies install
    psycopg = None
    dict_row = None

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT,
    status TEXT NOT NULL,
    total_items INTEGER DEFAULT 0,
    processed_items INTEGER DEFAULT 0,
    found_items INTEGER DEFAULT 0,
    retry_items INTEGER DEFAULT 0,
    failed_items INTEGER DEFAULT 0,
    not_discoverable_items INTEGER DEFAULT 0,
    requested_command TEXT DEFAULT 'NONE',
    worker_heartbeat_at TEXT,
    worker_id TEXT,
    worker_lease_until TEXT,
    last_error_type TEXT,
    last_error_message TEXT,
    pause_requested_at TEXT,
    paused_at TEXT,
    worker_started_at TEXT,
    telegram_account_phone TEXT,
    job_mode TEXT NOT NULL DEFAULT 'SINGLE',
    parent_job_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS check_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    original_phone TEXT NOT NULL,
    normalized_phone TEXT,
    status TEXT NOT NULL,
    attempt_count INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 5,
    next_retry_at TEXT,
    last_error_type TEXT,
    last_error_message TEXT,
    telegram_user_id INTEGER,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    user_was_online TEXT,
    cleanup_error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    in_flight_started_at TEXT,
    ownership_lost_at TEXT,
    recovery_after TEXT,
    processing_token TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_check_items_job_status
ON check_items(job_id, status);

CREATE INDEX IF NOT EXISTS idx_check_items_retry
ON check_items(status, next_retry_at);

CREATE INDEX IF NOT EXISTS idx_check_items_normalized
ON check_items(job_id, normalized_phone);

CREATE TABLE IF NOT EXISTS account_runtime_state (
    account_key TEXT PRIMARY KEY,
    blocked_until TEXT,
    last_request_at TEXT,
    last_rate_limit_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_worker_state (
    account_key TEXT PRIMARY KEY,
    worker_id TEXT,
    worker_heartbeat_at TEXT,
    worker_lease_until TEXT,
    job_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_accounts (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    encrypted INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_sessions (
    account_id TEXT PRIMARY KEY,
    session_ciphertext TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_login_sessions (
    session_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    state TEXT NOT NULL,
    phone_code_hash_ciphertext TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS web_login_security (
    client_key TEXT PRIMARY KEY,
    failure_count INTEGER NOT NULL DEFAULT 0,
    window_started_at TEXT,
    blocked_until TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manager_security_messages (
    account_id TEXT NOT NULL,
    tg_msg_id TEXT NOT NULL,
    message_ciphertext TEXT NOT NULL,
    received_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(account_id, tg_msg_id)
);

CREATE TABLE IF NOT EXISTS manager_audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    account_id TEXT,
    status TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);

"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT,
    status TEXT NOT NULL,
    total_items INTEGER DEFAULT 0,
    processed_items INTEGER DEFAULT 0,
    found_items INTEGER DEFAULT 0,
    retry_items INTEGER DEFAULT 0,
    failed_items INTEGER DEFAULT 0,
    not_discoverable_items INTEGER DEFAULT 0,
    requested_command TEXT DEFAULT 'NONE',
    worker_heartbeat_at TEXT,
    worker_id TEXT,
    worker_lease_until TEXT,
    last_error_type TEXT,
    last_error_message TEXT,
    pause_requested_at TEXT,
    paused_at TEXT,
    worker_started_at TEXT,
    telegram_account_phone TEXT,
    job_mode TEXT NOT NULL DEFAULT 'SINGLE',
    parent_job_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS check_items (
    id BIGSERIAL PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    original_phone TEXT NOT NULL,
    normalized_phone TEXT,
    status TEXT NOT NULL,
    attempt_count INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 5,
    next_retry_at TEXT,
    last_error_type TEXT,
    last_error_message TEXT,
    telegram_user_id BIGINT,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    user_was_online TEXT,
    cleanup_error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    in_flight_started_at TEXT,
    ownership_lost_at TEXT,
    recovery_after TEXT,
    processing_token TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_check_items_job_status
ON check_items(job_id, status);

CREATE INDEX IF NOT EXISTS idx_check_items_retry
ON check_items(status, next_retry_at);

CREATE INDEX IF NOT EXISTS idx_check_items_normalized
ON check_items(job_id, normalized_phone);

CREATE TABLE IF NOT EXISTS account_runtime_state (
    account_key TEXT PRIMARY KEY,
    blocked_until TEXT,
    last_request_at TEXT,
    last_rate_limit_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_worker_state (
    account_key TEXT PRIMARY KEY,
    worker_id TEXT,
    worker_heartbeat_at TEXT,
    worker_lease_until TEXT,
    job_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_accounts (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    encrypted INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_sessions (
    account_id TEXT PRIMARY KEY,
    session_ciphertext TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_login_sessions (
    session_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    state TEXT NOT NULL,
    phone_code_hash_ciphertext TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS web_login_security (
    client_key TEXT PRIMARY KEY,
    failure_count INTEGER NOT NULL DEFAULT 0,
    window_started_at TEXT,
    blocked_until TEXT,
    updated_at TEXT NOT NULL
);

"""

# PostgreSQL schema migrations are explicit and versioned.  CREATE TABLE IF
# NOT EXISTS does not upgrade an older table, so each release that adds runtime
# columns must include idempotent ALTER statements here.
POSTGRES_MIGRATIONS = [
    (
        "001_runtime_lease_and_fencing",
        [
            "ALTER TABLE check_items ADD COLUMN IF NOT EXISTS in_flight_started_at TEXT",
            "ALTER TABLE check_items ADD COLUMN IF NOT EXISTS ownership_lost_at TEXT",
            "ALTER TABLE check_items ADD COLUMN IF NOT EXISTS recovery_after TEXT",
            "ALTER TABLE check_items ADD COLUMN IF NOT EXISTS processing_token TEXT",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS requested_command TEXT DEFAULT 'NONE'",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS worker_heartbeat_at TEXT",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS worker_id TEXT",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS worker_lease_until TEXT",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS last_error_type TEXT",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS last_error_message TEXT",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS pause_requested_at TEXT",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS paused_at TEXT",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS worker_started_at TEXT",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS telegram_account_phone TEXT",
            "CREATE TABLE IF NOT EXISTS account_runtime_state (account_key TEXT PRIMARY KEY, blocked_until TEXT, last_request_at TEXT, last_rate_limit_at TEXT, updated_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS account_worker_state (account_key TEXT PRIMARY KEY, worker_id TEXT, worker_heartbeat_at TEXT, worker_lease_until TEXT, job_id TEXT, updated_at TEXT NOT NULL)",
        ],
    ),
    (
        "002_multi_telegram_accounts",
        [
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS telegram_account_phone TEXT",
            "CREATE TABLE IF NOT EXISTS telegram_accounts (id TEXT PRIMARY KEY, label TEXT NOT NULL, phone TEXT NOT NULL UNIQUE, is_default INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_login_at TEXT)",
        ],
    ),
    (
        "003_parallel_multi_account_jobs",
        [
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS job_mode TEXT NOT NULL DEFAULT 'SINGLE'",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS parent_job_id TEXT",
            "CREATE INDEX IF NOT EXISTS idx_jobs_parent ON jobs(parent_job_id)",
        ],
    ),
    (
        "004_sql_persistence_everywhere",
        [
            "CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, encrypted INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS telegram_sessions (account_id TEXT PRIMARY KEY, session_ciphertext TEXT NOT NULL, updated_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS telegram_login_sessions (session_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, state TEXT NOT NULL, phone_code_hash_ciphertext TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS web_sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT)",
            "CREATE TABLE IF NOT EXISTS web_login_security (client_key TEXT PRIMARY KEY, failure_count INTEGER NOT NULL DEFAULT 0, window_started_at TEXT, blocked_until TEXT, updated_at TEXT NOT NULL)",
        ],
    ),
    (
        "005_unified_manager_persistence",
        [
            "CREATE TABLE IF NOT EXISTS manager_security_messages (account_id TEXT NOT NULL, tg_msg_id TEXT NOT NULL, message_ciphertext TEXT NOT NULL, received_at TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(account_id, tg_msg_id))",
            "CREATE TABLE IF NOT EXISTS manager_audit_logs (id BIGSERIAL PRIMARY KEY, action TEXT NOT NULL, account_id TEXT, status TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL)",
        ],
    ),
]

#: Lightweight, idempotent migrations for databases created before these
#: columns existed. ALTER TABLE ... ADD COLUMN is cheap and safe to re-run is
#: guarded by checking pragma table_info first.
MIGRATIONS = [
    (
        "check_items",
        "in_flight_started_at",
        "ALTER TABLE check_items ADD COLUMN in_flight_started_at TEXT",
    ),
    (
        "check_items",
        "ownership_lost_at",
        "ALTER TABLE check_items ADD COLUMN ownership_lost_at TEXT",
    ),
    (
        "check_items",
        "recovery_after",
        "ALTER TABLE check_items ADD COLUMN recovery_after TEXT",
    ),
    (
        "check_items",
        "processing_token",
        "ALTER TABLE check_items ADD COLUMN processing_token TEXT",
    ),
    (
        "jobs",
        "requested_command",
        "ALTER TABLE jobs ADD COLUMN requested_command TEXT DEFAULT 'NONE'",
    ),
    (
        "jobs",
        "worker_heartbeat_at",
        "ALTER TABLE jobs ADD COLUMN worker_heartbeat_at TEXT",
    ),
    (
        "jobs",
        "worker_id",
        "ALTER TABLE jobs ADD COLUMN worker_id TEXT",
    ),
    (
        "jobs",
        "worker_lease_until",
        "ALTER TABLE jobs ADD COLUMN worker_lease_until TEXT",
    ),
    (
        "jobs",
        "last_error_type",
        "ALTER TABLE jobs ADD COLUMN last_error_type TEXT",
    ),
    (
        "jobs",
        "last_error_message",
        "ALTER TABLE jobs ADD COLUMN last_error_message TEXT",
    ),
    (
        "jobs",
        "pause_requested_at",
        "ALTER TABLE jobs ADD COLUMN pause_requested_at TEXT",
    ),
    (
        "jobs",
        "paused_at",
        "ALTER TABLE jobs ADD COLUMN paused_at TEXT",
    ),
    (
        "jobs",
        "worker_started_at",
        "ALTER TABLE jobs ADD COLUMN worker_started_at TEXT",
    ),
    (
        "jobs",
        "telegram_account_phone",
        "ALTER TABLE jobs ADD COLUMN telegram_account_phone TEXT",
    ),
    (
        "jobs",
        "job_mode",
        "ALTER TABLE jobs ADD COLUMN job_mode TEXT NOT NULL DEFAULT 'SINGLE'",
    ),
    (
        "jobs",
        "parent_job_id",
        "ALTER TABLE jobs ADD COLUMN parent_job_id TEXT",
    ),
]


class Database:
    """Database facade used by the repositories.

    The application uses PostgreSQL whenever ``database_url`` is supplied.
    SQLite remains available only when an explicit local path is passed,
    which keeps the isolated legacy unit tests self-contained.
    """

    def __init__(
        self, path: Optional[Path] = None, database_url: Optional[str] = None
    ):
        self.path = Path(path) if path is not None else Path("data/checker.db")
        self.database_url = database_url
        self._use_postgres = bool(database_url)
        self._lock = threading.RLock()
        self._transaction_depth = 0

        if self._use_postgres:
            if psycopg is None:
                raise RuntimeError(
                    "PostgreSQL support requires the psycopg[binary] package."
                )
            self._conn = self._new_postgres_connection()
        else:
            if self.path.parent:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            if self._use_postgres:
                with self._conn.cursor() as cur:
                    for statement in POSTGRES_SCHEMA.split(";"):
                        if statement.strip():
                            cur.execute(statement)
                    self._run_postgres_migrations(cur)
                self._conn.commit()
            else:
                self._conn.executescript(SCHEMA)
                self._run_migrations()
                self._conn.commit()

    def _run_postgres_migrations(self, cur) -> None:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        for version, statements in POSTGRES_MIGRATIONS:
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = %s", (version,))
            if cur.fetchone() is not None:
                continue
            for statement in statements:
                cur.execute(statement)
            from .models import now_iso

            cur.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s)",
                (version, now_iso()),
            )

    def _column_exists(self, table: str, column: str) -> bool:
        if self._use_postgres:
            row = self._conn.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                  AND column_name = %s
                """,
                (table, column),
            ).fetchone()
            return row is not None
        cols = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(c["name"] == column for c in cols)

    def _run_migrations(self) -> None:
        for table, column, ddl in MIGRATIONS:
            try:
                exists = self._column_exists(table, column)
            except Exception:
                exists = True
            if not exists:
                self._conn.execute(ddl)

    def _new_postgres_connection(self):
        return psycopg.connect(
            self.database_url, row_factory=dict_row, connect_timeout=5
        )

    def _connection_lost(self, exc: Exception) -> bool:
        if not self._use_postgres or psycopg is None:
            return False
        return isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError))

    def _reconnect_postgres(self) -> None:
        if not self._use_postgres:
            return
        try:
            self._conn.close()
        except Exception:
            pass
        self._conn = self._new_postgres_connection()

    def _ensure_postgres_connection(self) -> None:
        if not self._use_postgres:
            return
        if getattr(self._conn, "closed", False) or getattr(self._conn, "broken", False):
            if getattr(self, "_transaction_depth", 0):
                raise RuntimeError("PostgreSQL connection lost during active transaction.")
            self._reconnect_postgres()

    def connect(self):
        with self._lock:
            self._ensure_postgres_connection()
            return self._conn

    def _adapt_sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self._use_postgres else sql

    def execute(self, sql: str, params: tuple = ()):
        with self._lock:
            statement = self._adapt_sql(sql)
            self._ensure_postgres_connection()
            try:
                return self._conn.execute(statement, params)
            except Exception as exc:
                if not self._connection_lost(exc) or getattr(self, "_transaction_depth", 0):
                    raise
                self._reconnect_postgres()
                return self._conn.execute(statement, params)

    def executemany(self, sql: str, params_list) -> None:
        with self._lock:
            statement = self._adapt_sql(sql)
            values = list(params_list)
            self._ensure_postgres_connection()

            def run_many() -> None:
                cursor = self._conn.cursor()
                try:
                    cursor.executemany(statement, values)
                finally:
                    cursor.close()

            try:
                run_many()
                if not getattr(self, "_transaction_depth", 0):
                    self._conn.commit()
            except Exception as exc:
                if not self._connection_lost(exc) or getattr(self, "_transaction_depth", 0):
                    raise
                self._reconnect_postgres()
                run_many()
                self._conn.commit()

    def commit(self) -> None:
        with self._lock:
            if getattr(self, "_transaction_depth", 0):
                return
            self._ensure_postgres_connection()
            try:
                self._conn.commit()
            except Exception as exc:
                if self._connection_lost(exc):
                    try:
                        self._reconnect_postgres()
                    except Exception:
                        pass
                raise

    @contextmanager
    def transaction(self):
        """Atomic unit of work; repository commit() calls are deferred inside it."""
        with self._lock:
            outermost = self._transaction_depth == 0
            self._transaction_depth += 1
            try:
                yield self
            except Exception:
                self._transaction_depth -= 1
                if outermost:
                    self._conn.rollback()
                raise
            else:
                self._transaction_depth -= 1
                if outermost:
                    self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if not self._use_postgres:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(FULL);")
                except Exception:
                    pass
            try:
                self._conn.close()
            except Exception:
                pass
