"""Shared state + FastAPI dependencies for the web UI."""

import hashlib
import logging
import os
from pathlib import Path

from fastapi import HTTPException, Request

from ..config import Config
from ..database import Database
from ..database_config import database_source
from ..maintenance import cleanup_retention
from ..repositories.manager_repository import ManagerRepository
from .account_manager import AccountManager
from .auth import COOKIE_NAME, AuthService
from .runner import JobRunner
from .sql_state import bootstrap_and_apply_sql_state
from .sse import SSEHub

logger = logging.getLogger(__name__)


def _database_fingerprint(url: str | None) -> str:
    if not url:
        return "none"
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]

class WebContext:
    def __init__(self, config: Config):
        self.config = config
        default_path = Path(os.getenv("DATABASE_PATH", "data/checker.db"))
        uses_default_storage = config.database_path == default_path
        require_postgres = os.getenv("WEB_REQUIRE_POSTGRES", "true").lower() == "true"
        if uses_default_storage and require_postgres and not config.database_url:
            raise RuntimeError(
                "DATABASE_URL is required for the production Web UI; "
                "local SQLite fallback is disabled."
            )
        database_url = config.database_url if uses_default_storage else None
        self.db = Database(config.db_path, database_url=database_url)
        try:
            audit_days = int(os.getenv("AUDIT_RETENTION_DAYS", "90"))
            security_days = int(os.getenv("SECURITY_MESSAGE_RETENTION_DAYS", "30"))
        except ValueError:
            audit_days, security_days = 90, 30
        self.audit_retention_days = audit_days
        self.security_message_retention_days = security_days
        self.retention_cleanup = cleanup_retention(self.db, audit_days, security_days)
        self.secret_box, self.settings = bootstrap_and_apply_sql_state(self.db, config)
        self.database_source = database_source() if database_url else "sqlite"
        self.database_fingerprint = _database_fingerprint(database_url)
        self.sse = SSEHub()
        self.auth = AuthService(config, self.db, self.settings)
        self.account = AccountManager(config, self.sse, self.db, self.secret_box)
        self.manager_store = ManagerRepository(self.db, self.secret_box)
        self.runner = JobRunner(config, self.db, self.sse, account_manager=self.account)

    def close(self) -> None:
        self.account.shutdown()
        self.db.close()


def get_context(request: Request) -> WebContext:
    ctx = getattr(request.app.state, "web_context", None)
    if ctx is None:
        raise HTTPException(status_code=503, detail="Web context chưa sẵn sàng.")
    return ctx


def require_user(request: Request) -> str:
    ctx = get_context(request)
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập.")
    username = ctx.auth.verify_token(token)
    if username is None:
        raise HTTPException(
            status_code=401, detail="Phiên đã hết hạn, vui lòng đăng nhập lại."
        )
    return username
