import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from ..database import Database
from ..models import now_iso, parse_iso


def _fernet_from_master(master_key: str) -> Fernet:
    if not master_key:
        raise RuntimeError("PERSISTENCE_MASTER_KEY is required for encrypted SQL persistence.")
    key = base64.urlsafe_b64encode(hashlib.sha256(master_key.encode("utf-8")).digest())
    return Fernet(key)


class SecretBox:
    def __init__(self, master_key: str):
        self._fernet = _fernet_from_master(master_key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("Encrypted SQL value cannot be decrypted with current master key.") from exc

class SettingsRepository:
    def __init__(self, db: Database, box: SecretBox):
        self.db, self.box = db, box

    def set(self, key: str, value: str, encrypted: bool = False) -> None:
        stored = self.box.encrypt(value) if encrypted else value
        now = now_iso()
        self.db.execute(
            "INSERT INTO app_settings(key,value,encrypted,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, encrypted=excluded.encrypted, updated_at=excluded.updated_at",
            (key, stored, 1 if encrypted else 0, now),
        )
        self.db.commit()

    def get(self, key: str) -> Optional[str]:
        row = self.db.execute("SELECT value, encrypted FROM app_settings WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        return self.box.decrypt(row["value"]) if row["encrypted"] else row["value"]

    def all(self) -> dict[str, str]:
        rows = self.db.execute("SELECT key,value,encrypted FROM app_settings").fetchall()
        return {r["key"]: self.box.decrypt(r["value"]) if r["encrypted"] else r["value"] for r in rows}


class TelegramSessionRepository:
    def __init__(self, db: Database, box: SecretBox):
        self.db, self.box = db, box

    def save(self, account_id: str, session_string: str) -> None:
        self.db.execute(
            "INSERT INTO telegram_sessions(account_id,session_ciphertext,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(account_id) DO UPDATE SET session_ciphertext=excluded.session_ciphertext, updated_at=excluded.updated_at",
            (account_id, self.box.encrypt(session_string), now_iso()),
        )
        self.db.commit()

    def load(self, account_id: str) -> Optional[str]:
        row = self.db.execute("SELECT session_ciphertext FROM telegram_sessions WHERE account_id=?", (account_id,)).fetchone()
        return self.box.decrypt(row["session_ciphertext"]) if row else None

    def delete(self, account_id: str) -> None:
        self.db.execute("DELETE FROM telegram_sessions WHERE account_id=?", (account_id,))
        self.db.commit()

class TelegramLoginRepository:
    def __init__(self, db: Database, box: SecretBox):
        self.db, self.box = db, box

    def create(self, session_id: str, account_id: str, state: str, phone_code_hash: Optional[str], ttl_seconds: int = 600) -> dict:
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
        self.db.execute(
            "INSERT INTO telegram_login_sessions(session_id,account_id,state,phone_code_hash_ciphertext,error,created_at,updated_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",
            (session_id, account_id, state, self.box.encrypt(phone_code_hash) if phone_code_hash else None, None, now_iso(), now_iso(), expires),
        )
        self.db.commit()
        return self.get(session_id)

    def get(self, session_id: str) -> Optional[dict]:
        row = self.db.execute("SELECT * FROM telegram_login_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        cipher = d.pop("phone_code_hash_ciphertext", None)
        d["phone_code_hash"] = self.box.decrypt(cipher) if cipher else None
        return d

    def update(self, session_id: str, state: str, error: Optional[str] = None, phone_code_hash: Optional[str] = None) -> None:
        if phone_code_hash is None:
            self.db.execute("UPDATE telegram_login_sessions SET state=?, error=?, updated_at=? WHERE session_id=?", (state, error, now_iso(), session_id))
        else:
            self.db.execute("UPDATE telegram_login_sessions SET state=?, error=?, phone_code_hash_ciphertext=?, updated_at=? WHERE session_id=?", (state, error, self.box.encrypt(phone_code_hash), now_iso(), session_id))
        self.db.commit()

    def delete(self, session_id: str) -> None:
        self.db.execute("DELETE FROM telegram_login_sessions WHERE session_id=?", (session_id,))
        self.db.commit()

    def active_for_account(self, account_id: str) -> Optional[dict]:
        row = self.db.execute(
            "SELECT session_id FROM telegram_login_sessions WHERE account_id=? AND state IN ('WAIT_CODE','WAIT_2FA') AND expires_at>? ORDER BY created_at DESC LIMIT 1",
            (account_id, now_iso()),
        ).fetchone()
        return self.get(row["session_id"]) if row else None

class WebSessionRepository:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self, username: str, ttl_seconds: int) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
        self.db.execute("INSERT INTO web_sessions(token_hash,username,created_at,expires_at,revoked_at) VALUES(?,?,?,?,NULL)", (self.token_hash(token), username, now_iso(), expires))
        self.db.commit()
        return token

    def verify(self, token: str) -> Optional[str]:
        row = self.db.execute("SELECT username,expires_at,revoked_at FROM web_sessions WHERE token_hash=?", (self.token_hash(token),)).fetchone()
        if not row or row["revoked_at"] or not row["expires_at"] or row["expires_at"] <= now_iso():
            return None
        return row["username"]

    def revoke(self, token: str) -> None:
        self.db.execute("UPDATE web_sessions SET revoked_at=? WHERE token_hash=?", (now_iso(), self.token_hash(token)))
        self.db.commit()

    def purge_expired(self) -> None:
        self.db.execute("DELETE FROM web_sessions WHERE expires_at<=? OR revoked_at IS NOT NULL", (now_iso(),))
        self.db.commit()


class LoginSecurityRepository:
    def __init__(self, db: Database):
        self.db = db

    def retry_after(self, client_key: str) -> int:
        row = self.db.execute("SELECT blocked_until FROM web_login_security WHERE client_key=?", (client_key,)).fetchone()
        if not row or not row["blocked_until"]:
            return 0
        target = parse_iso(row["blocked_until"])
        if target is None:
            return 0
        return max(0, int((target - datetime.now(timezone.utc)).total_seconds()))

    def record_failure(self, client_key: str, limit: int, window_seconds: int, block_seconds: int) -> None:
        now = datetime.now(timezone.utc)
        row = self.db.execute("SELECT failure_count,window_started_at,blocked_until FROM web_login_security WHERE client_key=?", (client_key,)).fetchone()
        count = 0
        window_start = now
        if row and row["window_started_at"]:
            parsed = parse_iso(row["window_started_at"])
            if parsed and (now - parsed).total_seconds() <= window_seconds:
                count = int(row["failure_count"] or 0)
                window_start = parsed
        count += 1
        blocked_until = None
        if count >= limit:
            blocked_until = (now + timedelta(seconds=block_seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
            count = 0
            window_start = now
        self.db.execute(
            "INSERT INTO web_login_security(client_key,failure_count,window_started_at,blocked_until,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(client_key) DO UPDATE SET failure_count=excluded.failure_count,window_started_at=excluded.window_started_at,blocked_until=excluded.blocked_until,updated_at=excluded.updated_at",
            (client_key, count, window_start.isoformat(timespec="seconds").replace("+00:00", "Z"), blocked_until, now_iso()),
        )
        self.db.commit()

    def clear(self, client_key: str) -> None:
        self.db.execute("DELETE FROM web_login_security WHERE client_key=?", (client_key,))
        self.db.commit()
