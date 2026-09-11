import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from telegram_phone_number_checker.config import Config
from telegram_phone_number_checker.database import Database
from telegram_phone_number_checker.repositories.persistence_repository import SecretBox
from telegram_phone_number_checker.webapi.account_manager import AccountManager
from telegram_phone_number_checker.webapi.sse import SSEHub


class FakeQR:
    url = "tg://login?token=test-token"
    expires = datetime.now(timezone.utc)

    async def wait(self):
        await asyncio.Event().wait()


class FakeClient:
    def __init__(self, *_args, **_kwargs):
        self.session = object()
        self.disconnected = False

    async def connect(self):
        return None

    async def disconnect(self):
        self.disconnected = True

    async def qr_login(self):
        return FakeQR()

    async def get_me(self):
        return SimpleNamespace(phone="84912345678", username="qruser", first_name="QR")


@pytest.mark.asyncio
async def test_qr_start_uses_memory_session_only(tmp_path, monkeypatch):
    cfg = Config(); cfg.api_id = "1"; cfg.api_hash = "hash"; cfg.api_phone_number = None
    cfg.database_url = None; cfg.database_path = tmp_path / "qr.db"; cfg.proxy = None
    db = Database(cfg.database_path)
    manager = AccountManager(cfg, SSEHub(), db, SecretBox("test-master"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("telegram_phone_number_checker.webapi.account_manager.TelegramClient", FakeClient)
    result = await manager.start_qr_login()
    assert result["state"] == "WAIT_SCAN"
    assert result["url"].startswith("tg://login?token=")
    assert not list(tmp_path.glob("*.session"))
    await manager.cancel_qr(result["qr_id"])
    db.close()


@pytest.mark.asyncio
async def test_qr_finalize_encrypts_string_session_in_sql(tmp_path, monkeypatch):
    cfg = Config(); cfg.api_id = "1"; cfg.api_hash = "hash"; cfg.api_phone_number = None
    cfg.database_url = None; cfg.database_path = tmp_path / "qr-final.db"; cfg.proxy = None
    db = Database(cfg.database_path)
    manager = AccountManager(cfg, SSEHub(), db, SecretBox("test-master"))
    client = FakeClient()
    manager._qr_pending["q1"] = {"client": client, "state": "WAIT_SCAN", "error": None, "account_id": None}
    monkeypatch.setattr("telegram_phone_number_checker.webapi.account_manager.StringSession.save", lambda _session: "qr-secret-session")
    result = await manager._finalize_qr("q1")
    assert result["state"] == "AUTHORIZED"
    row = db.execute("SELECT session_ciphertext FROM telegram_sessions").fetchone()
    assert row and row["session_ciphertext"] != "qr-secret-session"
    account = db.execute("SELECT phone FROM telegram_accounts").fetchone()
    assert account and account["phone"] == "+84912345678"
    assert client.disconnected is True
    assert not list(tmp_path.glob("*.session"))
    db.close()
