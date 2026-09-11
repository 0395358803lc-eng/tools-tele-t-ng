from datetime import datetime, timedelta, timezone

from telegram_phone_number_checker.database import Database
from telegram_phone_number_checker.maintenance import cleanup_retention
from telegram_phone_number_checker.models import now_iso


def _old(days: int) -> str:
    value = datetime.now(timezone.utc) - timedelta(days=days)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def test_retention_removes_only_expired_transient_rows(tmp_path):
    db = Database(tmp_path / "retention.db")
    db.execute("INSERT INTO telegram_accounts(id,label,phone,is_default,created_at,updated_at) VALUES(?,?,?,?,?,?)", ("a1","A","+84911111111",1,now_iso(),now_iso()))
    db.execute("INSERT INTO telegram_sessions(account_id,session_ciphertext,updated_at) VALUES(?,?,?)", ("a1","cipher",now_iso()))
    db.execute("INSERT INTO manager_audit_logs(action,account_id,status,detail,created_at) VALUES(?,?,?,?,?)", ("old",None,"ok",None,_old(120)))
    db.execute("INSERT INTO manager_audit_logs(action,account_id,status,detail,created_at) VALUES(?,?,?,?,?)", ("new",None,"ok",None,now_iso()))
    db.execute("INSERT INTO web_sessions(token_hash,username,created_at,expires_at,revoked_at) VALUES(?,?,?,?,?)", ("expired","u",_old(2),_old(1),None))
    db.commit()
    counts = cleanup_retention(db, audit_days=90, security_message_days=30)
    assert counts["manager_audit_logs"] == 1
    assert counts["web_sessions"] == 1
    assert db.execute("SELECT COUNT(*) AS c FROM manager_audit_logs").fetchone()["c"] == 1
    assert db.execute("SELECT COUNT(*) AS c FROM web_sessions").fetchone()["c"] == 0
    assert db.execute("SELECT COUNT(*) AS c FROM telegram_sessions").fetchone()["c"] == 1
    assert db.execute("SELECT COUNT(*) AS c FROM telegram_accounts").fetchone()["c"] == 1
    db.close()
