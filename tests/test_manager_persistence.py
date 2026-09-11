"""Regression tests for unified manager SQL persistence."""

import httpx
import pytest

from telegram_phone_number_checker.config import Config
from telegram_phone_number_checker.database import Database
from telegram_phone_number_checker.repositories.manager_repository import ManagerRepository
from telegram_phone_number_checker.repositories.persistence_repository import SecretBox
from telegram_phone_number_checker.webapi.app import create_app


@pytest.fixture
def manager_db(tmp_path):
    db = Database(tmp_path / "manager.db")
    try:
        yield db
    finally:
        db.close()


def test_security_messages_are_encrypted_at_rest(manager_db):
    repo = ManagerRepository(manager_db, SecretBox("manager-test-master-key"))
    secret_text = "Login code 12345 from Telegram"
    repo.upsert_security_messages("account-a", [{"id": 77, "text": secret_text, "date": "2026-09-11T00:00:00Z"}])
    row = manager_db.execute("SELECT message_ciphertext FROM manager_security_messages WHERE account_id=?", ("account-a",)).fetchone()
    assert row is not None
    assert secret_text not in row["message_ciphertext"]
    assert repo.security_messages("account-a")[0]["text"] == secret_text

def test_manager_audit_is_persisted(manager_db):
    repo = ManagerRepository(manager_db, SecretBox("manager-test-master-key"))
    repo.audit("profile.update", "account-a", "ok", "changed")
    repo.audit("profile.username", "account-a", "ok", "changed again")
    rows = repo.recent_audit()
    assert rows[0]["action"] == "profile.username"
    assert rows[0]["account_id"] == "account-a"
    assert rows[0]["status"] == "ok"
    assert isinstance(rows[0]["id"], int)
    assert isinstance(rows[1]["id"], int)
    assert rows[0]["id"] == rows[1]["id"] + 1


@pytest.mark.asyncio
async def test_ui_theme_is_shared_between_independent_clients(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_UI_USERNAME", "themeadmin")
    monkeypatch.setenv("WEB_UI_PASSWORD", "themepass123")
    monkeypatch.setenv("WEB_UI_SECRET_KEY", "theme-secret-key")
    monkeypatch.setenv("WEB_UI_COOKIE_SECURE", "false")
    cfg = Config()
    cfg.api_id = None
    cfg.api_hash = None
    cfg.api_phone_number = None
    cfg.proxy = None
    cfg.database_url = None
    cfg.database_path = tmp_path / "theme.db"
    app = create_app(cfg)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as a, httpx.AsyncClient(transport=transport, base_url="http://test") as b:
            for client in (a, b):
                r = await client.post("/api/auth/login", json={"username": "themeadmin", "password": "themepass123"})
                assert r.status_code == 200

            r = await a.put("/api/config/ui", json={"theme": "light"})
            assert r.status_code == 200
            assert r.json()["theme"] == "light"
            r = await b.get("/api/config/ui")
            assert r.status_code == 200
            assert r.json()["theme"] == "light"


def test_management_service_uses_shared_account_lease(tmp_path):
    from telegram_phone_number_checker.rate_limiter import account_key_from_phone
    from telegram_phone_number_checker.repositories.job_repository import JobRepository
    from telegram_phone_number_checker.webapi.account_manager import AccountManager
    from telegram_phone_number_checker.webapi.sse import SSEHub

    cfg = Config(); cfg.database_url = None; cfg.database_path = tmp_path / "lease.db"
    cfg.api_id = "1"; cfg.api_hash = "hash"; cfg.api_phone_number = "+84999999999"
    db = Database(cfg.database_path)
    manager = AccountManager(cfg, SSEHub(), db, SecretBox("test-master"))
    account = manager._repo.get_default()
    service = manager.management_service(account["id"])
    service._management_claim()
    key = account_key_from_phone(account["phone"])
    repo = JobRepository(db)
    assert repo.is_account_owned(key) is True
    assert repo.claim_account(key, "job-worker", "job-1", 60) is False
    service._management_release()
    assert repo.claim_account(key, "job-worker", "job-1", 60) is True
    repo.release_account(key, "job-worker", "job-1")
    db.close()


def test_management_floodwait_persists_across_manager_restart(tmp_path):
    from telegram_phone_number_checker.webapi.account_manager import AccountManager
    from telegram_phone_number_checker.webapi.sse import SSEHub

    cfg = Config(); cfg.database_url = None; cfg.database_path = tmp_path / "rate.db"
    cfg.api_id = "1"; cfg.api_hash = "hash"; cfg.api_phone_number = "+84999999999"
    cfg.min_request_interval_seconds = 0.25
    db = Database(cfg.database_path)
    first = AccountManager(cfg, SSEHub(), db, SecretBox("test-master"))
    account = first._repo.get_default()
    limiter = first.management_service(account["id"])._management_rate_limiter
    assert limiter._min_interval == 0.25
    limiter.register_rate_limit(30)

    second = AccountManager(cfg, SSEHub(), db, SecretBox("test-master"))
    limiter2 = second.management_service(account["id"])._management_rate_limiter
    assert limiter2.is_blocked() is True
    assert limiter2.remaining_block_seconds() > 0
    db.close()


@pytest.mark.asyncio
async def test_account_health_exposes_persisted_floodwait(tmp_path):
    from telegram_phone_number_checker.rate_limiter import RateLimitManager, account_key_from_phone
    from telegram_phone_number_checker.webapi.account_manager import AccountManager
    from telegram_phone_number_checker.webapi.sse import SSEHub

    cfg = Config(); cfg.database_url = None; cfg.database_path = tmp_path / "health.db"
    cfg.api_id = "1"; cfg.api_hash = "hash"; cfg.api_phone_number = "+84999999999"
    db = Database(cfg.database_path)
    manager = AccountManager(cfg, SSEHub(), db, SecretBox("test-master"))
    account = manager._repo.get_default()
    limiter = RateLimitManager(db=db, account_key=account_key_from_phone(account["phone"]))
    limiter.register_rate_limit(60)
    health = await manager.status_for_id(account["id"])
    assert health["state"] == "FLOOD_WAIT"
    assert health["blocked_until"] is not None
    assert health["last_rate_limit_at"] is not None
    db.close()


def test_manager_audit_pagination_and_filters(manager_db):
    repo = ManagerRepository(manager_db, SecretBox("manager-test-master-key"))
    repo.audit("profile.update", "a1", "ok", "one")
    repo.audit("groups.join", "a2", "failed", "two")
    repo.audit("profile.update", "a1", "ok", "three")
    page = repo.recent_audit(limit=1, offset=1, action="profile.update", account_id="a1", status="ok")
    assert len(page) == 1
    assert page[0]["action"] == "profile.update"
    assert repo.count_audit(action="profile.update", account_id="a1", status="ok") == 2
