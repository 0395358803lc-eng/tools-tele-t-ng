import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@127.0.0.1/test")
os.environ.setdefault(
    "SESSION_SECRET", "test-session-secret-that-is-long-enough-for-tests-only"
)
os.environ.setdefault("APP_PASSWORD", "test-password-not-for-production")

from app import secrets_store  # noqa: E402
from app.auth import _client_ip  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import _safe_static_candidate  # noqa: E402


class FakeRequest:
    class Client:
        host = "127.0.0.1"

    client = Client()
    headers = {"x-forwarded-for": "203.0.113.55"}


class StaticPathSecurityTests(unittest.TestCase):
    def test_normal_static_file_is_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "static"
            root.mkdir()
            target = root / "index.html"
            target.write_text("ok")
            self.assertEqual(
                _safe_static_candidate(root, "index.html"), target.resolve()
            )

    def test_traversal_variants_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "static"
            root.mkdir()
            variants = [
                "../schema.sql",
                "%2e%2e/schema.sql",
                "%252e%252e%252fschema.sql",
                "..%2fschema.sql",
                "%2e%2e%5cschema.sql",
                "sub/../../schema.sql",
            ]
            for value in variants:
                with self.subTest(value=value):
                    self.assertIsNone(_safe_static_candidate(root, value))

    def test_x_forwarded_for_is_not_trusted_directly(self):
        self.assertEqual(_client_ip(FakeRequest()), "127.0.0.1")


class EncryptedTwoFaStoreTests(unittest.TestCase):
    def test_password_is_encrypted_in_database_not_local_file(self):
        old_dir = settings.SESSIONS_DIR
        old_secret = settings.SESSION_SECRET
        old_key = settings.TWOFA_ENCRYPTION_KEY
        rows = {}

        class FakeSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def get(self, model, key): return rows.get(key)
            def add(self, row): rows[row.phone] = row
            async def commit(self): return None

        class FakeFactory:
            def __call__(self): return FakeSession()

        try:
            with tempfile.TemporaryDirectory() as td, patch.object(
                secrets_store, "AsyncSessionLocal", FakeFactory()
            ):
                settings.SESSIONS_DIR = td
                settings.SESSION_SECRET = "unit-test-secret-with-sufficient-length-and-entropy-placeholder"
                settings.TWOFA_ENCRYPTION_KEY = ""
                secrets_store._migrated_legacy = False
                asyncio.run(secrets_store.save_2fa("+84123456789", "SensitivePassword123!"))
                token = bytes(rows["+84123456789"].ciphertext)
                self.assertNotIn(b"SensitivePassword123!", token)
                self.assertFalse((Path(td) / "twofa.enc").exists())
                self.assertFalse((Path(td) / "twofa.json").exists())
                self.assertEqual(
                    asyncio.run(secrets_store.get_2fa("+84123456789")),
                    "SensitivePassword123!",
                )
        finally:
            secrets_store._migrated_legacy = False
            settings.SESSIONS_DIR = old_dir
            settings.SESSION_SECRET = old_secret
            settings.TWOFA_ENCRYPTION_KEY = old_key


class AppAuthSecurityTests(unittest.TestCase):
    def setUp(self):
        import app.auth as auth

        self.auth = auth
        self.old = (
            settings.APP_PASSWORD,
            settings.SESSION_SECRET,
            settings.COOKIE_SECURE,
            settings.LOGIN_MAX_ATTEMPTS,
            settings.LOGIN_WINDOW_MIN,
        )
        settings.APP_PASSWORD = "unit-test-app-password"
        settings.SESSION_SECRET = "unit-test-cookie-secret-long-enough-for-signing"
        settings.COOKIE_SECURE = True
        settings.LOGIN_MAX_ATTEMPTS = 2
        settings.LOGIN_WINDOW_MIN = 15
        auth._pw_hash = None
        auth._signer = None
        auth._attempts.clear()

    def tearDown(self):
        import app.auth as auth

        (
            settings.APP_PASSWORD,
            settings.SESSION_SECRET,
            settings.COOKIE_SECURE,
            settings.LOGIN_MAX_ATTEMPTS,
            settings.LOGIN_WINDOW_MIN,
        ) = self.old
        auth._pw_hash = None
        auth._signer = None
        auth._attempts.clear()

    @staticmethod
    def _request():
        from starlette.requests import Request

        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/auth-app/login",
                "headers": [(b"x-forwarded-for", b"203.0.113.10")],
                "client": ("127.0.0.1", 5555),
                "server": ("test", 443),
                "scheme": "https",
                "query_string": b"",
            }
        )

    def test_login_sets_secure_httponly_cookie(self):
        from fastapi import Response

        response = Response()
        result = asyncio.run(
            self.auth.login(
                self.auth.LoginIn(password="unit-test-app-password"),
                self._request(),
                response,
            )
        )
        self.assertTrue(result["ok"])
        cookie = response.headers.get("set-cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=lax", cookie)

    def test_failed_logins_are_rate_limited_by_resolved_client_ip(self):
        from fastapi import HTTPException, Response

        for _ in range(2):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(
                    self.auth.login(
                        self.auth.LoginIn(password="wrong"), self._request(), Response()
                    )
                )
            self.assertEqual(ctx.exception.status_code, 401)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(
                self.auth.login(
                    self.auth.LoginIn(password="wrong"), self._request(), Response()
                )
            )
        self.assertEqual(ctx.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
