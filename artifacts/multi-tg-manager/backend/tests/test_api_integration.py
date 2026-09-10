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

import httpx  # noqa: E402

import app.main as main_module  # noqa: E402
from app.auth import require_auth  # noqa: E402
from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas import SettingsIn  # noqa: E402


class ApiBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_health_is_public(self):
        response = await self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    async def test_data_api_requires_app_auth(self):
        response = await self.client.get("/api/accounts")
        self.assertEqual(response.status_code, 401)



    async def test_mutating_api_request_creates_audit_record(self):
        class FakeSession:
            def __init__(self):
                self.records = []
                self.committed = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def add(self, obj):
                self.records.append(obj)

            async def commit(self):
                self.committed = True

        fake = FakeSession()
        with patch.object(main_module, "AsyncSessionLocal", lambda: fake):
            response = await self.client.post("/api/not-a-real-route", json={})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(fake.committed)
        self.assertEqual(len(fake.records), 1)
        record = fake.records[0]
        self.assertEqual(record.method, "POST")
        self.assertEqual(record.path, "/api/not-a-real-route")
        self.assertEqual(record.status_code, 404)

    async def test_readiness_reports_missing_dependencies(self):
        with patch.object(main_module, "check_db", AsyncMock(return_value=False)):
            response = await self.client.get("/api/readiness")
        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertFalse(payload["ready"])
        self.assertIn("database", payload["reasons"])

    async def test_destructive_bulk_routes_require_explicit_confirmation(self):
        async def fake_db():
            yield None

        app.dependency_overrides[require_auth] = lambda: True
        app.dependency_overrides[get_db] = fake_db
        try:
            cases = (
                ("/api/groups/bulk_leave_all", {"account_ids": [], "confirm": False}),
                (
                    "/api/groups/bulk_delete_my_messages",
                    {"account_ids": [], "max_scan": 10, "confirm": False},
                ),
                (
                    "/api/messaging/bulk_wipe_chat",
                    {"account_ids": [], "target": "example", "confirm": False},
                ),
            )
            for path, body in cases:
                response = await self.client.post(path, json=body)
                self.assertEqual(response.status_code, 400, path)
                self.assertIn("confirmation", response.json()["detail"].lower())
        finally:
            app.dependency_overrides.clear()

    def test_settings_schema_rejects_invalid_rate_and_concurrency(self):
        with self.assertRaises(Exception):
            SettingsIn(
                rate_min=2,
                rate_max=1,
                concurrency=5,
                sessions_dir="sessions",
                auto_reconnect=True,
                notification_sound=True,
            )
        with self.assertRaises(Exception):
            SettingsIn(
                rate_min=0,
                rate_max=1,
                concurrency=51,
                sessions_dir="sessions",
                auto_reconnect=True,
                notification_sound=True,
            )


    async def test_oversized_session_upload_is_rejected_before_telegram(self):
        from app.config import settings

        old = settings.MAX_SESSION_UPLOAD_BYTES
        settings.MAX_SESSION_UPLOAD_BYTES = 4
        app.dependency_overrides[require_auth] = lambda: True
        try:
            response = await self.client.post(
                "/api/auth/import_sessions",
                files={"files": ("oversized.session", b"12345", "application/octet-stream")},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["failed"], 1)
            self.assertIn("exceeds", payload["results"][0]["detail"].lower())
        finally:
            settings.MAX_SESSION_UPLOAD_BYTES = old
            app.dependency_overrides.clear()

    async def test_legacy_base64_photo_endpoint_is_removed(self):
        app.dependency_overrides[require_auth] = lambda: True
        try:
            response = await self.client.get("/api/accounts/1/profile/photo_url")
            self.assertEqual(response.status_code, 404)
        finally:
            app.dependency_overrides.clear()

    async def test_encoded_static_traversal_is_rejected(self):
        for path in (
            "/%2e%2e/schema.sql",
            "/..%2fschema.sql",
            "/%252e%252e%252fschema.sql",
        ):
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 404, path)
            self.assertNotIn("PostgreSQL schema", response.text)
