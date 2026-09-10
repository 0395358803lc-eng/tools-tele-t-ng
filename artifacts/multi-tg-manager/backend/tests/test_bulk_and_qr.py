import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@127.0.0.1/test")
os.environ.setdefault(
    "SESSION_SECRET", "test-session-secret-that-is-long-enough-for-tests-only"
)

from app.config import settings  # noqa: E402
from app.routers import groups  # noqa: E402
from app.tg_manager import TgClientManager, manager  # noqa: E402
from app.utils import bulk_stream  # noqa: E402


class FakeClient:
    def __init__(self):
        self.disconnected = False

    async def disconnect(self):
        self.disconnected = True


class BulkEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_done_event_counts_partial_failed_and_skipped(self):
        old_clients = manager._clients
        old_min, old_max = settings.RATE_MIN, settings.RATE_MAX
        manager._clients = {1: object(), 2: object(), 3: object()}
        settings.RATE_MIN = settings.RATE_MAX = 0
        try:

            async def action(_client, aid):
                if aid == 1:
                    return "ok", "done"
                if aid == 2:
                    return "partial", "some completed"
                raise ValueError("boom")

            lines = []
            async for line in bulk_stream(
                [
                    (1, "+1", "one"),
                    (2, "+2", "two"),
                    (3, "+3", "three"),
                    (4, "+4", "four"),
                ],
                action,
                concurrency=4,
            ):
                lines.append(json.loads(line))
            done = lines[-1]
            self.assertEqual(done["type"], "done")
            self.assertEqual(done["success"], 1)
            self.assertEqual(done["partial"], 1)
            self.assertEqual(done["failed"], 1)
            self.assertEqual(done["skipped"], 1)
        finally:
            manager._clients = old_clients
            settings.RATE_MIN, settings.RATE_MAX = old_min, old_max


class QrLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_qr_cancel_awaits_cancelled_wait_task_cleanly(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as td:
            base = str(Path(td) / "qr_test")

            async def waiter():
                await asyncio.sleep(60)

            task = asyncio.create_task(waiter())
            manager._qr_pending["test"] = {
                "client": client,
                "wait_task": task,
                "session_path": base,
            }
            await manager.qr_cancel("test")
            self.assertTrue(task.cancelled())
            self.assertTrue(client.disconnected)
            self.assertNotIn("test", manager._qr_pending)


class ShutdownLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_cancels_background_tasks_and_disconnects_clients(self):
        local = TgClientManager()
        live = FakeClient()
        pending = FakeClient()
        qr = FakeClient()

        async def sleeper():
            await asyncio.sleep(60)

        bg = asyncio.create_task(sleeper())
        qr_wait = asyncio.create_task(sleeper())
        local._background_tasks.add(bg)
        local._clients[1] = live
        local._pending["+100"] = {"client": pending}
        local._qr_pending["qr"] = {
            "client": qr,
            "wait_task": qr_wait,
            "session_path": "/tmp/nonexistent-qr-session",
        }

        await local.shutdown()

        self.assertTrue(bg.cancelled())
        self.assertTrue(qr_wait.cancelled())
        self.assertTrue(live.disconnected)
        self.assertTrue(pending.disconnected)
        self.assertTrue(qr.disconnected)
        self.assertFalse(local._background_tasks)
        self.assertFalse(local._clients)
        self.assertFalse(local._pending)
        self.assertFalse(local._qr_pending)


class RefreshStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_reconnect_false_never_calls_connect(self):
        class Client:
            def __init__(self):
                self.connect_calls = 0
            def is_connected(self):
                return False
            async def connect(self):
                self.connect_calls += 1
            async def is_user_authorized(self):
                return True

        local = TgClientManager()
        cli = Client()
        local._clients[1] = cli
        local.auto_reconnect = False
        local._set_status = AsyncMock()
        local._sync_presence = AsyncMock()
        await local.refresh_status_all()
        self.assertEqual(cli.connect_calls, 0)
        local._set_status.assert_awaited_with(1, "disconnected")
        local._sync_presence.assert_not_awaited()

    async def test_auto_reconnect_true_reconnects_and_syncs_presence(self):
        me = object()
        class Client:
            def __init__(self):
                self.connected = False
                self.connect_calls = 0
            def is_connected(self):
                return self.connected
            async def connect(self):
                self.connect_calls += 1
                self.connected = True
            async def is_user_authorized(self):
                return True
            async def get_me(self):
                return me

        local = TgClientManager()
        cli = Client()
        local._clients[7] = cli
        local.auto_reconnect = True
        local._set_status = AsyncMock()
        local._sync_presence = AsyncMock()
        await local.refresh_status_all()
        self.assertEqual(cli.connect_calls, 1)
        local._set_status.assert_awaited_with(7, "connected")
        local._sync_presence.assert_awaited_once_with(7, me)


class JoinSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_capacity_error_is_propagated_without_auto_leave(self):
        capacity_error = type("ChannelsTooMuchError", (Exception,), {})()
        with patch.object(groups, "_join_with_client", AsyncMock(side_effect=capacity_error)) as join:
            with self.assertRaises(type(capacity_error)):
                await groups._join_handle(object(), "example")
        join.assert_awaited_once()


class RefreshScaleTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_500_clients_is_bounded_and_parallel(self):
        tracker = {"active": 0, "max": 0}
        lock = asyncio.Lock()
        me = object()

        class Client:
            def is_connected(self):
                return True
            async def is_user_authorized(self):
                async with lock:
                    tracker["active"] += 1
                    tracker["max"] = max(tracker["max"], tracker["active"])
                await asyncio.sleep(0.005)
                async with lock:
                    tracker["active"] -= 1
                return True
            async def get_me(self):
                return me

        local = TgClientManager()
        local._clients = {i: Client() for i in range(1, 501)}
        local._set_status = AsyncMock()
        local._sync_presence = AsyncMock()
        await local.refresh_status_all()
        self.assertGreater(tracker["max"], 1)
        self.assertLessEqual(tracker["max"], 20)
        self.assertEqual(local._set_status.await_count, 500)
        self.assertEqual(local._sync_presence.await_count, 500)
