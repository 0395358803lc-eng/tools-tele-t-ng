import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import session_store
from app.config import settings


class SessionBlobRoundTripTests(unittest.TestCase):
    def test_sqlite_session_round_trip_through_encrypted_blob(self):
        old_secret = settings.SESSION_SECRET
        old_key = settings.TWOFA_ENCRYPTION_KEY
        rows = {}

        class FakeSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def get(self, model, key): return rows.get(key)
            def add(self, row): rows[row.account_id] = row
            async def commit(self): return None

        class FakeFactory:
            def __call__(self): return FakeSession()

        try:
            settings.SESSION_SECRET = "portable-session-test-secret-long-enough"
            settings.TWOFA_ENCRYPTION_KEY = ""
            with tempfile.TemporaryDirectory() as td, patch.object(
                session_store, "AsyncSessionLocal", FakeFactory()
            ):
                src = Path(td) / "account.session"
                con = sqlite3.connect(src)
                con.execute("create table demo (value text)")
                con.execute("insert into demo values ('portable')")
                con.commit()
                con.close()

                self.assertTrue(asyncio.run(session_store.save_file(7, str(src))))
                self.assertIn(7, rows)
                self.assertNotIn(b"portable", bytes(rows[7].ciphertext))

                src.unlink()
                self.assertTrue(asyncio.run(session_store.restore_file(7, str(src))))
                con = sqlite3.connect(src)
                value = con.execute("select value from demo").fetchone()[0]
                con.close()
                self.assertEqual(value, "portable")
        finally:
            settings.SESSION_SECRET = old_secret
            settings.TWOFA_ENCRYPTION_KEY = old_key
