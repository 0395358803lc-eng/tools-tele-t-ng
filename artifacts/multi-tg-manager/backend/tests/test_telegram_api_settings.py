import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@127.0.0.1/test")
os.environ.setdefault(
    "SESSION_SECRET", "test-session-secret-that-is-long-enough-for-tests-only"
)
os.environ.setdefault("APP_PASSWORD", "test-password-not-for-production")

from fastapi import HTTPException  # noqa: E402

import app.main as main_module  # noqa: E402
from app import telegram_api_store  # noqa: E402
from app.config import settings  # noqa: E402
from app.routers import settings as settings_router  # noqa: E402
from app.schemas import TelegramApiCredentialsIn  # noqa: E402
from app.tg_manager import TgClientManager  # noqa: E402


class FakeDb:
    def __init__(self):
        self.rows = {}
        self.commits = 0

    async def get(self, model, key):
        return self.rows.get(key)

    def add(self, row):
        self.rows[row.key] = row

    async def commit(self):
        self.commits += 1


class TelegramApiStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old = (
            settings.TG_API_ID,
            settings.TG_API_HASH,
            settings.TWOFA_ENCRYPTION_KEY,
        )
        settings.TWOFA_ENCRYPTION_KEY = "test-app-encryption-key-32-characters-minimum"
        settings.TG_API_ID = 0
        settings.TG_API_HASH = ""

    async def asyncTearDown(self):
        settings.TG_API_ID, settings.TG_API_HASH, settings.TWOFA_ENCRYPTION_KEY = self.old

    async def test_credentials_are_encrypted_and_hash_is_never_returned(self):
        db = FakeDb()
        api_hash = "a" * 32
        await telegram_api_store.save(db, 123456, api_hash)
        stored = db.rows[telegram_api_store.SETTING_KEY].value
        self.assertNotIn(api_hash, stored)
        self.assertNotIn("123456", stored)

        state = await telegram_api_store.status(db)
        self.assertEqual(state["api_id"], 123456)
        self.assertTrue(state["api_hash_set"])
        self.assertNotIn("api_hash", state)

        settings.TG_API_ID = 0
        settings.TG_API_HASH = ""
        self.assertTrue(await telegram_api_store.load(db, persist_env_bootstrap=False))
        self.assertEqual(settings.TG_API_ID, 123456)
        self.assertEqual(settings.TG_API_HASH, api_hash)

    async def test_first_configuration_reconnects_saved_clients(self):
        db = FakeDb()
        body = TelegramApiCredentialsIn(api_id=111111, api_hash="b" * 32)
        with patch.object(
            settings_router.manager,
            "rebuild_all_clients_for_api_credentials",
            AsyncMock(return_value={"reconnected": 3, "failed": 1}),
        ) as reload_mock:
            result = await settings_router.update_telegram_api(body, db)
        reload_mock.assert_awaited_once()
        self.assertTrue(result.configured)
        self.assertEqual(result.reconnected, 3)
        self.assertEqual(result.failed, 1)
        self.assertFalse(result.reconnect_required)

    async def test_changing_api_id_requires_new_hash(self):
        db = FakeDb()
        await telegram_api_store.save(db, 111111, "c" * 32)
        body = TelegramApiCredentialsIn(api_id=222222, api_hash="")
        with self.assertRaises(HTTPException) as ctx:
            await settings_router.update_telegram_api(body, db)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("required when TG_API_ID changes", ctx.exception.detail)

    async def test_replacement_can_leave_existing_clients_running(self):
        db = FakeDb()
        await telegram_api_store.save(db, 111111, "d" * 32)
        body = TelegramApiCredentialsIn(api_id=222222, api_hash="e" * 32)
        with patch.object(
            settings_router.manager,
            "rebuild_all_clients_for_api_credentials",
            AsyncMock(return_value={"reconnected": 9, "failed": 0}),
        ) as reload_mock:
            result = await settings_router.update_telegram_api(body, db)
        reload_mock.assert_not_awaited()
        self.assertTrue(result.reconnect_required)
        self.assertEqual(result.api_id, 222222)

    async def test_explicit_reconnect_rebuilds_even_when_pair_is_unchanged(self):
        db = FakeDb()
        await telegram_api_store.save(db, 111111, "f" * 32)
        body = TelegramApiCredentialsIn(
            api_id=111111, api_hash="", reconnect_existing=True
        )
        with patch.object(
            settings_router.manager,
            "rebuild_all_clients_for_api_credentials",
            AsyncMock(return_value={"reconnected": 2, "failed": 0}),
        ) as reload_mock:
            result = await settings_router.update_telegram_api(body, db)
        reload_mock.assert_awaited_once()
        self.assertEqual(result.reconnected, 2)
        self.assertFalse(result.reconnect_required)

    async def test_rebuild_result_reports_per_account_status(self):
        from unittest.mock import AsyncMock, patch

        class ScalarResult:
            def scalars(self): return self
            def all(self):
                return [
                    type("A", (), {"id": 1, "phone": "+100"})(),
                    type("A", (), {"id": 2, "phone": "+200"})(),
                ]

        class Db:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def execute(self, _q): return ScalarResult()

        local = TgClientManager()
        old_id, old_hash = settings.TG_API_ID, settings.TG_API_HASH
        settings.TG_API_ID, settings.TG_API_HASH = 12345, "a" * 32
        try:
            async def start(acc):
                if acc.id == 2:
                    raise RuntimeError("not authorized")
                local._clients[acc.id] = object()

            with patch("app.tg_manager.AsyncSessionLocal", lambda: Db()), \
                 patch.object(local, "stop_client", AsyncMock()), \
                 patch.object(local, "start_client", side_effect=start):
                result = await local.rebuild_all_clients_for_api_credentials()
            self.assertEqual(result["reconnected"], 1)
            self.assertEqual(result["failed"], 1)
            self.assertEqual([r["status"] for r in result["results"]], ["ok", "failed"])
            self.assertNotIn("a" * 32, str(result))
        finally:
            settings.TG_API_ID, settings.TG_API_HASH = old_id, old_hash


class TelegramApiStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_without_telegram_credentials_skips_client_restore(self):
        local = TgClientManager()
        old = (settings.TG_API_ID, settings.TG_API_HASH)
        settings.TG_API_ID = 0
        settings.TG_API_HASH = ""
        try:
            await local.startup_load_all()
            self.assertFalse(local._clients)
            self.assertFalse(local._background_tasks)
        finally:
            settings.TG_API_ID, settings.TG_API_HASH = old

    async def test_readiness_can_be_green_before_telegram_is_configured(self):
        old = (
            settings.TG_API_ID,
            settings.TG_API_HASH,
            settings.APP_PASSWORD,
            settings.SESSION_SECRET,
        )
        settings.TG_API_ID = 0
        settings.TG_API_HASH = ""
        settings.APP_PASSWORD = "test-password-long-enough"
        settings.SESSION_SECRET = "test-session-secret-long-enough-for-readiness"
        try:
            with patch.object(main_module, "check_db", AsyncMock(return_value=True)), patch.object(
                main_module.session_store, "all_accounts_backed_up", AsyncMock(return_value=True)
            ):
                response = await main_module.readiness()
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'"telegram_configured":false', response.body)
        finally:
            (
                settings.TG_API_ID,
                settings.TG_API_HASH,
                settings.APP_PASSWORD,
                settings.SESSION_SECRET,
            ) = old

    async def test_readiness_fails_when_an_account_lacks_db_session_blob(self):
        old = (settings.APP_PASSWORD, settings.SESSION_SECRET)
        settings.APP_PASSWORD = "test-password-long-enough"
        settings.SESSION_SECRET = "test-session-secret-long-enough-for-readiness"
        try:
            with patch.object(main_module, "check_db", AsyncMock(return_value=True)), patch.object(
                main_module.session_store, "all_accounts_backed_up", AsyncMock(return_value=False)
            ):
                response = await main_module.readiness()
            self.assertEqual(response.status_code, 503)
            self.assertIn(b'"portable_session_store"', response.body)
        finally:
            settings.APP_PASSWORD, settings.SESSION_SECRET = old
