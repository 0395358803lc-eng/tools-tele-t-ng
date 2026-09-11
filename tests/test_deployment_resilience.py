import asyncio
import threading

import httpx
import psycopg
import pytest

import telegram_phone_number_checker.database as db_module
import telegram_phone_number_checker.webapi.app as app_module
from telegram_phone_number_checker.database import Database


class _DeadConnection:
    closed = False
    broken = False

    def execute(self, *_args, **_kwargs):
        raise psycopg.OperationalError("connection terminated")

    def close(self):
        self.closed = True


class _HealthyConnection:
    closed = False
    broken = False

    def execute(self, statement, params=()):
        return (statement, params)

    def close(self):
        self.closed = True

def test_postgres_execute_reconnects_after_disconnect(monkeypatch):
    healthy = _HealthyConnection()
    monkeypatch.setattr(db_module.psycopg, "connect", lambda *_a, **_k: healthy)

    db = object.__new__(Database)
    db.path = None
    db.database_url = "postgresql://example.invalid/db"
    db._use_postgres = True
    db._lock = threading.RLock()
    db._conn = _DeadConnection()

    result = db.execute("SELECT 1", ())
    assert result == ("SELECT 1", ())
    assert db._conn is healthy


@pytest.mark.asyncio
async def test_production_fast_start_survives_db_init_failure(monkeypatch):
    def fail_context(_cfg):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(app_module, "WebContext", fail_context)
    app = app_module.create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0)
            health = await client.get("/api/health")
            ready = await client.get("/api/ready")
            assert health.status_code == 200
            assert ready.status_code == 503
            assert ready.json()["status"] == "starting"


def test_postgres_connection_loss_does_not_reconnect_mid_transaction(monkeypatch):
    reconnects = []
    monkeypatch.setattr(
        db_module.psycopg,
        "connect",
        lambda *_a, **_k: reconnects.append(True),
    )
    db = object.__new__(Database)
    db.path = None
    db.database_url = "postgresql://example.invalid/db"
    db._use_postgres = True
    db._lock = threading.RLock()
    db._transaction_depth = 1
    db._conn = _DeadConnection()

    with pytest.raises(psycopg.OperationalError):
        db.execute("UPDATE jobs SET status='FAILED'", ())
    assert reconnects == []
