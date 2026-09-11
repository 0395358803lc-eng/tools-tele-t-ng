import pytest

from telegram_phone_number_checker.config import Config
from telegram_phone_number_checker.database import Database
from telegram_phone_number_checker.rate_limiter import account_key_from_phone
from telegram_phone_number_checker.repositories.job_repository import JobRepository
from telegram_phone_number_checker.webapi.account_manager import (
    AccountAlreadyBusy,
    AccountManager,
)
from telegram_phone_number_checker.webapi.sse import SSEHub, event_stream


@pytest.mark.asyncio
async def test_account_in_use_never_opens_second_telegram_client(tmp_path, monkeypatch):
    cfg = Config(); cfg.database_url = None; cfg.database_path = tmp_path / "account.db"
    cfg.api_phone_number = "+84999999999"
    db = Database(cfg.database_path)
    repo = JobRepository(db); repo.create("job", name="job", total_items=0)
    key = account_key_from_phone(cfg.api_phone_number)
    assert repo.claim_account(key, "worker", "job", 60)
    manager = AccountManager(cfg, SSEHub(), db)
    monkeypatch.setattr(manager, "_new_service", lambda: (_ for _ in ()).throw(AssertionError("must not open Telethon")))
    assert (await manager.status())["state"] == "IN_USE"
    with pytest.raises(AccountAlreadyBusy):
        await manager.start_login()
    with pytest.raises(AccountAlreadyBusy):
        await manager.logout()
    db.close()


@pytest.mark.asyncio
async def test_account_status_cache_avoids_reopening_service(tmp_path, monkeypatch):
    cfg = Config(); cfg.database_url = None; cfg.database_path = tmp_path / "cache.db"
    cfg.api_phone_number = "+84999999999"
    db = Database(cfg.database_path); manager = AccountManager(cfg, SSEHub(), db)
    calls = {"n": 0}

    class FakeService:
        async def is_authorized(self):
            calls["n"] += 1
            return True

    monkeypatch.setattr(manager, "_new_service", lambda: FakeService())
    assert (await manager.status())["state"] == "AUTHORIZED"
    assert (await manager.status())["state"] == "AUTHORIZED"
    assert calls["n"] == 1
    db.close()


@pytest.mark.asyncio
async def test_sse_subscriber_is_removed_on_disconnect():
    hub = SSEHub(); stream = event_stream(hub)
    assert (await anext(stream)).startswith(": connected")
    assert len(hub._subscribers) == 1
    await stream.aclose()
    assert len(hub._subscribers) == 0
