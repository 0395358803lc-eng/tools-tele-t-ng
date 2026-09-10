import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import session_store
from app.config import settings


class SessionStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_encrypted_database_snapshot_restores_sqlite_session(self):
        rows = {}

        class FakeSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def get(self, model, key): return rows.get(key)
            def add(self, row): rows[row.account_id] = row
            async def commit(self): return None

        class FakeFactory:
            def __call__(self): return FakeSession()

        old_key = settings.TWOFA_ENCRYPTION_KEY
        old_secret = settings.SESSION_SECRET
        settings.TWOFA_ENCRYPTION_KEY = ""
        settings.SESSION_SECRET = "portable-session-test-secret-long-enough"
        try:
            with tempfile.TemporaryDirectory() as td, patch.object(
                session_store, "AsyncSessionLocal", FakeFactory()
            ):
                source = Path(td) / "source.session"
                conn = sqlite3.connect(source)
                conn.execute("create table sample(value text)")
                conn.execute("insert into sample values ('ok')")
                conn.commit()
                conn.close()

                self.assertTrue(await session_store.save_file(7, str(source)))
                row = rows[7]
                self.assertNotIn(b"SQLite format 3", bytes(row.ciphertext))

                os.unlink(source)
                self.assertTrue(await session_store.restore_file(7, str(source)))
                restored = sqlite3.connect(source)
                try:
                    value = restored.execute("select value from sample").fetchone()[0]
                finally:
                    restored.close()
                self.assertEqual(value, "ok")
        finally:
            settings.TWOFA_ENCRYPTION_KEY = old_key
            settings.SESSION_SECRET = old_secret
