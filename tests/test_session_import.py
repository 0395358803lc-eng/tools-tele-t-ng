from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.crypto import AuthKey
from telethon.sessions import SQLiteSession, StringSession

from telegram_phone_number_checker.config import Config
from telegram_phone_number_checker.database import Database
from telegram_phone_number_checker.repositories.account_repository import AccountRepository
from telegram_phone_number_checker.repositories.persistence_repository import SecretBox
from telegram_phone_number_checker.session_importer import (
    SessionImportError,
    inspect_and_convert_session,
    validate_telethon_sqlite,
)
from telegram_phone_number_checker.webapi.account_manager import AccountManager
from telegram_phone_number_checker.webapi.sse import SSEHub


def make_session(path: Path) -> None:
    session = SQLiteSession(str(path))
    session.set_dc(2, "149.154.167.51", 443)
    session.auth_key = AuthKey(b"x" * 256)
    session.save()
    session.close()


def test_validate_rejects_non_sqlite(tmp_path):
    path = tmp_path / "bad.session"
    path.write_bytes(b"not a sqlite session")
    with pytest.raises(SessionImportError):
        validate_telethon_sqlite(path)


@pytest.mark.asyncio
async def test_convert_valid_session_without_network(tmp_path, monkeypatch):
    path = tmp_path / "valid.session"
    make_session(path)

    class FakeClient:
        def __init__(self, session, *_args, **_kwargs):
            self.session = session
        async def connect(self): pass
        async def disconnect(self): pass
        async def is_user_authorized(self): return True
        async def get_me(self):
            return SimpleNamespace(phone="84912345678", id=123, username="owner", first_name="Owner")

    monkeypatch.setattr("telegram_phone_number_checker.session_importer.TelegramClient", FakeClient)
    result = await inspect_and_convert_session(path, "1", "hash")
    assert result["phone"] == "84912345678"
    assert result["telegram_user_id"] == 123
    assert StringSession(result["session_string"]).auth_key is not None


@pytest.mark.asyncio
async def test_manager_import_encrypts_session_in_sql(tmp_path, monkeypatch):
    cfg = Config(); cfg.api_id = "1"; cfg.api_hash = "hash"; cfg.api_phone_number = None
    cfg.database_url = None; cfg.database_path = tmp_path / "db.sqlite"
    db = Database(cfg.database_path)
    manager = AccountManager(cfg, SSEHub(), db, SecretBox("test-master"))
    async def fake_import(*_args, **_kwargs):
        return {"session_string": "secret-session-value", "phone": "84912345678", "telegram_user_id": 1, "username": "u", "first_name": "U"}
    monkeypatch.setattr("telegram_phone_number_checker.webapi.account_manager.inspect_and_convert_session", fake_import)
    result = await manager.import_session_file(tmp_path / "anything.session", label="Imported")
    assert result["state"] == "AUTHORIZED"
    account = AccountRepository(db).get_by_phone("+84912345678")
    row = db.execute("SELECT session_ciphertext FROM telegram_sessions WHERE account_id=?", (account["id"],)).fetchone()
    assert row and row["session_ciphertext"] != "secret-session-value"
    db.close()


@pytest.mark.asyncio
async def test_manager_rejects_session_for_wrong_target(tmp_path, monkeypatch):
    cfg = Config(); cfg.api_id = "1"; cfg.api_hash = "hash"; cfg.api_phone_number = None
    cfg.database_url = None; cfg.database_path = tmp_path / "db.sqlite"
    db = Database(cfg.database_path)
    repo = AccountRepository(db)
    target = repo.create("a1", "+84911111111", "Target")
    manager = AccountManager(cfg, SSEHub(), db, SecretBox("test-master"))
    async def fake_import(*_args, **_kwargs):
        return {"session_string": "secret", "phone": "84922222222", "telegram_user_id": 2, "username": None, "first_name": None}
    monkeypatch.setattr("telegram_phone_number_checker.webapi.account_manager.inspect_and_convert_session", fake_import)
    with pytest.raises(SessionImportError):
        await manager.import_session_file(tmp_path / "x.session", account_id=target["id"])
    assert db.execute("SELECT COUNT(*) AS c FROM telegram_sessions").fetchone()["c"] == 0
    db.close()


@pytest.mark.asyncio
async def test_session_import_rolls_back_account_when_session_save_fails(tmp_path, monkeypatch):
    cfg = Config(); cfg.api_id = "1"; cfg.api_hash = "hash"; cfg.api_phone_number = None
    cfg.database_url = None; cfg.database_path = tmp_path / "rollback.sqlite"
    db = Database(cfg.database_path)
    manager = AccountManager(cfg, SSEHub(), db, SecretBox("test-master"))

    async def fake_import(*_args, **_kwargs):
        return {"session_string": "secret", "phone": "84912345678", "telegram_user_id": 3, "username": "u", "first_name": "U"}

    monkeypatch.setattr("telegram_phone_number_checker.webapi.account_manager.inspect_and_convert_session", fake_import)
    monkeypatch.setattr(manager._session_store, "save", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("save failed")))
    with pytest.raises(RuntimeError, match="save failed"):
        await manager.import_session_file(tmp_path / "x.session", label="Atomic")

    assert AccountRepository(db).get_by_phone("+84912345678") is None
    assert db.execute("SELECT COUNT(*) AS c FROM telegram_sessions").fetchone()["c"] == 0
    db.close()
