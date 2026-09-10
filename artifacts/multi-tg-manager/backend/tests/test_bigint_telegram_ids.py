import unittest

from sqlalchemy import BigInteger, insert
from sqlalchemy.dialects.postgresql.asyncpg import dialect

from app.models import Account, GoneAccount, SecurityMessage


class TelegramBigIntModelTests(unittest.TestCase):
    def test_telegram_ids_use_bigint(self):
        self.assertIsInstance(Account.__table__.c.tg_user_id.type, BigInteger)
        self.assertIsInstance(GoneAccount.__table__.c.tg_user_id.type, BigInteger)
        self.assertIsInstance(SecurityMessage.__table__.c.tg_msg_id.type, BigInteger)

    def test_account_insert_binds_large_telegram_id_as_bigint(self):
        stmt = insert(Account).values(
            phone="+10000000000",
            tg_user_id=7_124_460_226,
            session_file="test_session",
        )
        sql = str(stmt.compile(dialect=dialect()))
        self.assertIn("::BIGINT", sql)


if __name__ == "__main__":
    unittest.main()
