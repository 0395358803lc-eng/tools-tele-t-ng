"""Resolve the server-local production PostgreSQL URL."""

from __future__ import annotations

import os


def resolved_database_url() -> str | None:
    value = os.getenv("DATABASE_URL")
    return value.strip() if value and value.strip() else None


def database_source() -> str:
    return "server_postgres" if resolved_database_url() else "sqlite"
