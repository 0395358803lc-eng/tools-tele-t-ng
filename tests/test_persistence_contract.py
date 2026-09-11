from pathlib import Path

import pytest

from telegram_phone_number_checker.config import Config
from telegram_phone_number_checker.webapi.deps import WebContext


def test_frontend_does_not_use_browser_persistence():
    root = Path("web/src")
    forbidden = ("localStorage", "sessionStorage", "indexedDB", "document.cookie")
    offenders = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path}:{token}")
    assert not offenders, offenders


def test_production_web_refuses_local_sqlite_fallback(monkeypatch):
    monkeypatch.setenv("WEB_REQUIRE_POSTGRES", "true")
    cfg = Config()
    cfg.database_url = None
    cfg.database_path = Path("data/checker.db")
    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        WebContext(cfg)
