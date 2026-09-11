"""Database-backed operator authentication for the Web UI."""

import hashlib
import hmac
import os
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..config import Config
from ..repositories.persistence_repository import (
    LoginSecurityRepository,
    SettingsRepository,
    WebSessionRepository,
)

COOKIE_NAME = "web_session"
TOKEN_MAX_AGE_SECONDS = int(os.environ.get("WEB_UI_SESSION_MAX_AGE_SECONDS", str(60 * 60 * 12)))


def _sha256(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def make_scrypt_hash(password: str) -> str:
    n, r, p = 16384, 8, 1
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"

def _verify_scrypt(password: str, encoded: str) -> bool:
    try:
        kind, n_raw, r_raw, p_raw, salt_hex, digest_hex = encoded.split("$", 5)
        if kind != "scrypt":
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        if n not in (16384, 32768, 65536) or r != 8 or p not in (1, 2):
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class AuthService:
    def __init__(self, config: Config, db=None, settings: SettingsRepository | None = None):
        self._db = db
        self._settings = settings
        self._username = self._get("WEB_UI_USERNAME", os.getenv("WEB_UI_USERNAME", "admin"))
        self._password = self._get("WEB_UI_PASSWORD") or os.getenv("WEB_UI_PASSWORD")
        self._password_scrypt = self._get("WEB_UI_PASSWORD_SCRYPT") or os.getenv("WEB_UI_PASSWORD_SCRYPT")
        self._password_hash = self._get("WEB_UI_PASSWORD_HASH") or os.getenv("WEB_UI_PASSWORD_HASH")
        production_sql = bool(db is not None and getattr(db, "_use_postgres", False))
        if not any((self._password, self._password_scrypt, self._password_hash)):
            if production_sql:
                raise RuntimeError(
                    "Production Web UI requires WEB_UI_PASSWORD_SCRYPT, "
                    "WEB_UI_PASSWORD_HASH or WEB_UI_PASSWORD."
                )
            self._password = secrets.token_urlsafe(12)
        self._session_max_age = int(self._get("WEB_UI_SESSION_MAX_AGE_SECONDS", str(TOKEN_MAX_AGE_SECONDS)))
        secure_default = "true" if production_sql else "false"
        secure_raw = self._get(
            "WEB_UI_COOKIE_SECURE",
            os.getenv("WEB_UI_COOKIE_SECURE", secure_default),
        )
        self._cookie_secure = str(secure_raw).lower() == "true"
        if production_sql and os.getenv("WEB_UI_ALLOW_INSECURE_COOKIE", "false").lower() != "true":
            self._cookie_secure = True
        self._login_failure_limit = int(self._get("WEB_UI_LOGIN_FAILURE_LIMIT", os.getenv("WEB_UI_LOGIN_FAILURE_LIMIT", "5")))
        self._login_window_seconds = int(self._get("WEB_UI_LOGIN_WINDOW_SECONDS", os.getenv("WEB_UI_LOGIN_WINDOW_SECONDS", "300")))
        self._login_block_seconds = int(self._get("WEB_UI_LOGIN_BLOCK_SECONDS", os.getenv("WEB_UI_LOGIN_BLOCK_SECONDS", "300")))
        self._sessions = WebSessionRepository(db) if db is not None else None
        self._compat_serializer = None if db is not None else URLSafeTimedSerializer(
            os.getenv("WEB_UI_SECRET_KEY", "local-test-secret"), salt="telegram-phone-checker-web"
        )
        self._security = LoginSecurityRepository(db) if db is not None else None
        self._login_failures: dict[str, list[float]] = {}
        self._login_blocked_until: dict[str, float] = {}

    def _get(self, key: str, default: str | None = None) -> str | None:
        if self._settings is None:
            return default
        value = self._settings.get(key)
        return value if value is not None else default

    def verify_credentials(self, username: str, password: str) -> bool:
        if not hmac.compare_digest(username, self._username):
            return False
        # SQL-backed strong credentials are canonical. A stale plaintext
        # WEB_UI_PASSWORD deployment secret must never override a password
        # that has already been migrated/reset in PostgreSQL.
        if self._password_scrypt is not None:
            return _verify_scrypt(password, self._password_scrypt)
        if self._password_hash is not None:
            return hmac.compare_digest(_sha256(password), self._password_hash.lower())
        if self._password is not None:
            return hmac.compare_digest(password, self._password)
        return False

    def login_retry_after(self, client_key: str) -> int:
        if self._security is not None:
            return self._security.retry_after(client_key)
        import time
        now = time.monotonic()
        blocked_until = self._login_blocked_until.get(client_key, 0.0)
        if blocked_until > now:
            return max(1, int(blocked_until - now))
        self._login_blocked_until.pop(client_key, None)
        recent = [ts for ts in self._login_failures.get(client_key, []) if now - ts <= self._login_window_seconds]
        self._login_failures[client_key] = recent
        return 0

    def record_login_failure(self, client_key: str) -> None:
        if self._security is not None:
            self._security.record_failure(client_key, self._login_failure_limit, self._login_window_seconds, self._login_block_seconds)
            return
        import time
        now = time.monotonic()
        recent = [ts for ts in self._login_failures.get(client_key, []) if now - ts <= self._login_window_seconds]
        recent.append(now)
        self._login_failures[client_key] = recent
        if len(recent) >= self._login_failure_limit:
            self._login_blocked_until[client_key] = now + self._login_block_seconds
            self._login_failures[client_key] = []

    def clear_login_failures(self, client_key: str) -> None:
        if self._security is not None:
            self._security.clear(client_key)
            return
        self._login_failures.pop(client_key, None)
        self._login_blocked_until.pop(client_key, None)

    def issue_token(self, username: str) -> str:
        if self._sessions is not None:
            self._sessions.purge_expired()
            return self._sessions.create(username, self._session_max_age)
        # Local/test compatibility only. Production always uses SQL-backed opaque sessions.
        return self._compat_serializer.dumps({"u": username})

    def verify_token(self, token: str) -> str | None:
        if self._sessions is not None:
            return self._sessions.verify(token)
        try:
            data = self._compat_serializer.loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
            return data.get("u")
        except (BadSignature, SignatureExpired):
            return None

    def revoke_token(self, token: str) -> None:
        if self._sessions is not None:
            self._sessions.revoke(token)
        else:
            return None

    @property
    def username(self) -> str:
        return self._username

    @property
    def session_max_age(self) -> int:
        return self._session_max_age

    @property
    def cookie_secure(self) -> bool:
        return self._cookie_secure
