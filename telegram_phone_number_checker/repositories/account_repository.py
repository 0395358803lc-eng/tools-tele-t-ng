from typing import Dict, List, Optional

from ..database import Database
from ..models import now_iso


class AccountRepository:
    """Persistent metadata for Telegram accounts.

    API_ID/API_HASH remain global application credentials; only per-account
    metadata is stored here. Canonical Telethon session secrets are encrypted
    in ``telegram_sessions``; local ``*.session`` files are legacy migration inputs.
    """

    def __init__(self, db: Database):
        self.db = db

    def list(self) -> List[Dict]:
        rows = self.db.execute(
            "SELECT id, label, phone, is_default, created_at, updated_at, "
            "last_login_at FROM telegram_accounts ORDER BY is_default DESC, created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def get(self, account_id: str) -> Optional[Dict]:
        row = self.db.execute(
            "SELECT * FROM telegram_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        return dict(row) if row else None
    def get_by_phone(self, phone: str) -> Optional[Dict]:
        row = self.db.execute(
            "SELECT * FROM telegram_accounts WHERE phone = ?", (phone,)
        ).fetchone()
        return dict(row) if row else None

    def get_default(self) -> Optional[Dict]:
        row = self.db.execute(
            "SELECT * FROM telegram_accounts WHERE is_default = 1 "
            "ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if row:
            return dict(row)
        row = self.db.execute(
            "SELECT * FROM telegram_accounts ORDER BY created_at LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def create(self, account_id: str, phone: str, label: Optional[str] = None) -> Dict:
        now = now_iso()
        has_default = self.get_default() is not None
        self.db.execute(
            "INSERT INTO telegram_accounts "
            "(id, label, phone, is_default, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, label or phone, phone, 0 if has_default else 1, now, now),
        )
        self.db.commit()
        return self.get(account_id)
    def set_default(self, account_id: str) -> None:
        if self.get(account_id) is None:
            raise ValueError("Không tìm thấy tài khoản Telegram.")
        now = now_iso()
        self.db.execute("UPDATE telegram_accounts SET is_default = 0, updated_at = ?", (now,))
        self.db.execute(
            "UPDATE telegram_accounts SET is_default = 1, updated_at = ? WHERE id = ?",
            (now, account_id),
        )
        self.db.commit()

    def mark_logged_in(self, account_id: str) -> None:
        now = now_iso()
        self.db.execute(
            "UPDATE telegram_accounts SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (now, now, account_id),
        )
        self.db.commit()

    def rename(self, account_id: str, label: str) -> None:
        self.db.execute(
            "UPDATE telegram_accounts SET label = ?, updated_at = ? WHERE id = ?",
            (label.strip(), now_iso(), account_id),
        )
        self.db.commit()

    def delete(self, account_id: str) -> None:
        row = self.get(account_id)
        if row is None:
            return
        was_default = bool(row.get("is_default"))
        self.db.execute("DELETE FROM telegram_accounts WHERE id = ?", (account_id,))
        self.db.commit()
        if was_default:
            replacement = self.get_default()
            if replacement:
                self.set_default(replacement["id"])
