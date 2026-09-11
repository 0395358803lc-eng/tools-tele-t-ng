"""Persistent state for the unified Telegram manager UI."""

from typing import Iterable

from ..database import Database
from ..models import now_iso
from .persistence_repository import SecretBox


class ManagerRepository:
    def __init__(self, db: Database, box: SecretBox):
        self.db = db
        self.box = box

    def audit(self, action: str, account_id: str | None, status: str = "ok", detail: str | None = None) -> None:
        safe_detail = (detail or "")[:500] or None
        self.db.execute(
            "INSERT INTO manager_audit_logs(action,account_id,status,detail,created_at) VALUES(?,?,?,?,?)",
            (action[:80], account_id, status[:24], safe_detail, now_iso()),
        )
        self.db.commit()

    def upsert_security_messages(self, account_id: str, messages: Iterable[dict]) -> None:
        for message in messages:
            msg_id = str(message.get("id") or "")
            if not msg_id:
                continue
            text = str(message.get("text") or "")
            self.db.execute(
                "INSERT INTO manager_security_messages(account_id,tg_msg_id,message_ciphertext,received_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(account_id,tg_msg_id) DO UPDATE SET message_ciphertext=excluded.message_ciphertext,received_at=excluded.received_at,updated_at=excluded.updated_at",
                (account_id, msg_id, self.box.encrypt(text), message.get("date"), now_iso()),
            )
        self.db.commit()

    def security_messages(self, account_id: str, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT tg_msg_id,message_ciphertext,received_at FROM manager_security_messages WHERE account_id=? ORDER BY received_at DESC, tg_msg_id DESC LIMIT ?",
            (account_id, max(1, min(int(limit), 100))),
        ).fetchall()
        return [
            {"id": int(r["tg_msg_id"]) if str(r["tg_msg_id"]).isdigit() else r["tg_msg_id"], "text": self.box.decrypt(r["message_ciphertext"]), "date": r["received_at"]}
            for r in rows
        ]

    def recent_audit(
        self, limit: int = 100, offset: int = 0, action: str | None = None,
        account_id: str | None = None, status: str | None = None,
    ) -> list[dict]:
        where, params = [], []
        if action:
            where.append("action = ?"); params.append(action)
        if account_id:
            where.append("account_id = ?"); params.append(account_id)
        if status:
            where.append("status = ?"); params.append(status)
        clause = " WHERE " + " AND ".join(where) if where else ""
        sql = "SELECT id,action,account_id,status,detail,created_at FROM manager_audit_logs" + clause + " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend((max(1, min(int(limit), 500)), max(0, int(offset))))
        rows = self.db.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def count_audit(self, action: str | None = None, account_id: str | None = None, status: str | None = None) -> int:
        where, params = [], []
        if action:
            where.append("action = ?"); params.append(action)
        if account_id:
            where.append("account_id = ?"); params.append(account_id)
        if status:
            where.append("status = ?"); params.append(status)
        clause = " WHERE " + " AND ".join(where) if where else ""
        row = self.db.execute("SELECT COUNT(*) AS c FROM manager_audit_logs" + clause, tuple(params)).fetchone()
        return int(row["c"] if row else 0)
