import pytest

from telegram_phone_number_checker.config import Config
from telegram_phone_number_checker.database import Database
from telegram_phone_number_checker.repositories.persistence_repository import SecretBox
from telegram_phone_number_checker.webapi.account_manager import AccountManager
from telegram_phone_number_checker.webapi.sse import SSEHub


@pytest.mark.asyncio
async def test_force_sql_login_bypasses_legacy_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config()
    cfg.database_url = None
    cfg.database_path = tmp_path / "app.db"
    cfg.api_id = "1"
    cfg.api_hash = "hash"
    cfg.api_phone_number = "+84999999999"
    db = Database(cfg.database_path)
    manager = AccountManager(cfg, SSEHub(), db, SecretBox("test-master"))
    account = manager._repo.get_default()
    (tmp_path / f"{account['phone']}.session").write_bytes(b"legacy")
    seen = {"force": None}

    class FakeService:
        async def request_login_code(self):
            return {"authorized": False, "phone_code_hash": "hash-1"}

    def fake_new(phone=None, force_sql=False):
        seen["force"] = force_sql
        return FakeService()

    monkeypatch.setattr(manager, "_new_service", fake_new)
    result = await manager.start_login(account["id"], force_sql=True)
    assert seen["force"] is True
    assert result["state"] == "WAIT_CODE"
    assert (tmp_path / f"{account['phone']}.session").exists()


@pytest.mark.asyncio
async def test_authorized_sql_login_removes_legacy_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config()
    cfg.database_url = None
    cfg.database_path = tmp_path / "app2.db"
    cfg.api_id = "1"
    cfg.api_hash = "hash"
    cfg.api_phone_number = "+84999999999"
    db = Database(cfg.database_path)
    manager = AccountManager(cfg, SSEHub(), db, SecretBox("test-master"))
    account = manager._repo.get_default()
    legacy = tmp_path / f"{account['phone']}.session"
    legacy.write_bytes(b"legacy")
    manager._session_store.save(account["phone"], "sql-session-placeholder")
    row = manager._login_repo.create("login-1", account["id"], "WAIT_CODE", "hash-1")

    class FakeService:
        async def sign_in_code(self, code, phone_code_hash):
            assert code == "12345"
            assert phone_code_hash == "hash-1"
            return {"authorized": True, "needs_password": False}

    monkeypatch.setattr(manager, "_new_service", lambda *args, **kwargs: FakeService())
    result = await manager.submit_code(row["session_id"], "12345")
    assert result["state"] == "COMPLETED"
    assert not legacy.exists()
    assert manager._session_backend(account["phone"]) == "SQL"


@pytest.mark.asyncio
async def test_logout_removes_sql_and_legacy_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config()
    cfg.database_url = None
    cfg.database_path = tmp_path / "app3.db"
    cfg.api_id = "1"
    cfg.api_hash = "hash"
    cfg.api_phone_number = "+84999999999"
    db = Database(cfg.database_path)
    manager = AccountManager(cfg, SSEHub(), db, SecretBox("test-master"))
    account = manager._repo.get_default()
    legacy = tmp_path / f"{account['phone']}.session"
    legacy.write_bytes(b"legacy")
    manager._session_store.save(account["phone"], "sql-session-placeholder")
    result = await manager.logout(account["id"])
    assert result["deleted"] is True
    assert not legacy.exists()
    assert manager._session_backend(account["phone"]) == "NONE"


@pytest.mark.asyncio
async def test_legacy_session_can_migrate_to_sql_without_otp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(); cfg.database_url = None; cfg.database_path = tmp_path / "migrate.db"
    cfg.api_id = "1"; cfg.api_hash = "hash"; cfg.api_phone_number = "+84999999999"
    db = Database(cfg.database_path)
    manager = AccountManager(cfg, SSEHub(), db, SecretBox("test-master"))
    account = manager._repo.get_default()
    legacy = tmp_path / f"{account['phone']}.session"
    legacy.write_bytes(b"legacy-session")

    async def fake_inspect(*_args, **_kwargs):
        return {"session_string": "migrated-sql-session", "phone": account["phone"].lstrip("+"), "telegram_user_id": 1, "username": "u", "first_name": "U"}

    monkeypatch.setattr("telegram_phone_number_checker.webapi.account_manager.inspect_and_convert_session", fake_inspect)
    result = await manager.migrate_legacy_session(account["id"])
    assert result["session_backend"] == "SQL"
    assert manager._session_store.load(account["phone"]) == "migrated-sql-session"
    assert not legacy.exists()
    db.close()
