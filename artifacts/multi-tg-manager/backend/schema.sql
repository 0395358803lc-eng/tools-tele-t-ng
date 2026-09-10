-- REFERENCE SCHEMA ONLY. Alembic migrations under backend/alembic/ are authoritative.
-- Multi TG Manager PostgreSQL schema.
-- Do not apply this file manually during normal deploys; use `python migrate.py`.

CREATE TABLE IF NOT EXISTS accounts (
    id SERIAL PRIMARY KEY,
    phone VARCHAR(32) NOT NULL UNIQUE,
    tg_user_id BIGINT,
    first_name VARCHAR(64) NOT NULL DEFAULT '',
    last_name VARCHAR(64) NOT NULL DEFAULT '',
    username VARCHAR(64) NOT NULL DEFAULT '',
    bio VARCHAR(140) NOT NULL DEFAULT '',
    session_file VARCHAR(255) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'disconnected',
    has_2fa BOOLEAN NOT NULL DEFAULT FALSE,
    is_online BOOLEAN NOT NULL DEFAULT FALSE,
    last_seen TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_accounts_phone ON accounts (phone);

CREATE TABLE IF NOT EXISTS security_messages (
    id SERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    tg_msg_id BIGINT NOT NULL,
    message_text TEXT NOT NULL,
    type VARCHAR(32) NOT NULL DEFAULT 'unknown',
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_security_messages_account_tg_msg
    ON security_messages (account_id, tg_msg_id);

CREATE TABLE IF NOT EXISTS gone_accounts (
    id SERIAL PRIMARY KEY,
    account_id INTEGER,
    tg_user_id BIGINT,
    phone VARCHAR(32) NOT NULL,
    first_name VARCHAR(64) NOT NULL DEFAULT '',
    last_name VARCHAR(64) NOT NULL DEFAULT '',
    username VARCHAR(64) NOT NULL DEFAULT '',
    old_serial INTEGER,
    reason VARCHAR(16) NOT NULL DEFAULT 'removed',
    gone_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_gone_accounts_phone ON gone_accounts (phone);

CREATE TABLE IF NOT EXISTS telegram_session_blobs (
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    ciphertext BYTEA NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
    key VARCHAR(64) PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    method VARCHAR(8) NOT NULL,
    path VARCHAR(255) NOT NULL,
    status_code INTEGER NOT NULL,
    client_ip VARCHAR(64) NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_audit_logs_created_at ON audit_logs (created_at);
