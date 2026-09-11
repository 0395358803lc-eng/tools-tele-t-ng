import httpx
import pytest

from telegram_phone_number_checker.config import Config
from telegram_phone_number_checker.webapi.app import create_app
from telegram_phone_number_checker.webapi.auth import AuthService, make_scrypt_hash


def test_scrypt_password_hash(monkeypatch):
    monkeypatch.delenv("WEB_UI_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_UI_PASSWORD_HASH", raising=False)
    monkeypatch.setenv("WEB_UI_PASSWORD_SCRYPT", make_scrypt_hash("correct-password"))
    auth = AuthService(Config())
    assert auth.verify_credentials("admin", "correct-password")
    assert not auth.verify_credentials("admin", "wrong-password")


@pytest.mark.asyncio
async def test_login_rate_limit_after_repeated_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_UI_USERNAME", "admin")
    monkeypatch.setenv("WEB_UI_PASSWORD", "correct-password")
    monkeypatch.setenv("WEB_UI_SECRET_KEY", "test-secret")
    monkeypatch.setenv("WEB_UI_COOKIE_SECURE", "false")
    cfg = Config(); cfg.database_path = tmp_path / "rate-limit.db"
    app = create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            for _ in range(5):
                r = await client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
                assert r.status_code == 401
            r = await client.post("/api/auth/login", json={"username": "admin", "password": "correct-password"})
            assert r.status_code == 429
            assert "Retry-After" in r.headers


def test_token_survives_auth_service_restart(monkeypatch):
    monkeypatch.setenv("WEB_UI_USERNAME", "admin")
    monkeypatch.setenv("WEB_UI_PASSWORD", "correct-password")
    monkeypatch.setenv("WEB_UI_SECRET_KEY", "stable-restart-secret")
    first = AuthService(Config())
    token = first.issue_token("admin")
    second = AuthService(Config())
    assert second.verify_token(token) == "admin"


@pytest.mark.asyncio
async def test_secure_cookie_login_and_logout(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_UI_USERNAME", "admin")
    monkeypatch.setenv("WEB_UI_PASSWORD", "correct-password")
    monkeypatch.setenv("WEB_UI_SECRET_KEY", "test-secret")
    monkeypatch.setenv("WEB_UI_COOKIE_SECURE", "true")
    cfg = Config(); cfg.database_path = tmp_path / "secure-cookie.db"; cfg.database_url = None
    app = create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        async with app.router.lifespan_context(app):
            r = await client.post("/api/auth/login", json={"username": "admin", "password": "correct-password"})
            assert r.status_code == 200
            cookie = r.headers.get("set-cookie", "").lower()
            assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
            assert (await client.get("/api/auth/me")).status_code == 200
            assert (await client.post("/api/auth/logout")).status_code == 200
            assert (await client.get("/api/auth/me")).status_code == 401


def test_expired_token_is_rejected(monkeypatch):
    import time
    import telegram_phone_number_checker.webapi.auth as auth_module

    monkeypatch.setenv("WEB_UI_PASSWORD", "correct-password")
    monkeypatch.setenv("WEB_UI_SECRET_KEY", "expiry-test-secret")
    monkeypatch.setattr(auth_module, "TOKEN_MAX_AGE_SECONDS", 1)
    auth = AuthService(Config())
    token = auth.issue_token("admin")
    assert auth.verify_token(token) == "admin"
    time.sleep(2.1)
    assert auth.verify_token(token) is None


def test_sql_scrypt_overrides_stale_plaintext_env(tmp_path, monkeypatch):
    from telegram_phone_number_checker.database import Database
    from telegram_phone_number_checker.repositories.persistence_repository import SecretBox, SettingsRepository

    monkeypatch.setenv("WEB_UI_USERNAME", "admin")
    monkeypatch.setenv("WEB_UI_PASSWORD", "stale-deployment-secret")
    monkeypatch.delenv("WEB_UI_PASSWORD_SCRYPT", raising=False)
    monkeypatch.delenv("WEB_UI_PASSWORD_HASH", raising=False)

    db = Database(tmp_path / "auth-priority.db")
    settings = SettingsRepository(db, SecretBox("test-master"))
    settings.set("WEB_UI_USERNAME", "admin")
    settings.set("WEB_UI_PASSWORD_SCRYPT", make_scrypt_hash("database-password"))

    auth = AuthService(Config(), db=db, settings=settings)
    assert auth.verify_credentials("admin", "database-password")
    assert not auth.verify_credentials("admin", "stale-deployment-secret")
    db.close()


class _PostgresAuthDb:
    _use_postgres = True


def test_postgres_auth_requires_configured_password(monkeypatch):
    for key in ("WEB_UI_PASSWORD", "WEB_UI_PASSWORD_HASH", "WEB_UI_PASSWORD_SCRYPT"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="Production Web UI requires"):
        AuthService(Config(), db=_PostgresAuthDb())


def test_postgres_auth_forces_secure_cookie(monkeypatch):
    monkeypatch.setenv("WEB_UI_PASSWORD", "configured-password")
    monkeypatch.setenv("WEB_UI_COOKIE_SECURE", "false")
    monkeypatch.delenv("WEB_UI_ALLOW_INSECURE_COOKIE", raising=False)
    auth = AuthService(Config(), db=_PostgresAuthDb())
    assert auth.cookie_secure is True


@pytest.mark.asyncio
async def test_security_headers_and_cross_site_write_block(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_UI_USERNAME", "admin")
    monkeypatch.setenv("WEB_UI_PASSWORD", "correct-password")
    monkeypatch.setenv("WEB_UI_COOKIE_SECURE", "true")
    cfg = Config()
    cfg.database_path = tmp_path / "headers.db"
    cfg.database_url = None
    app = create_app(cfg)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        async with app.router.lifespan_context(app):
            health = await client.get("/api/health")
            assert health.headers["x-content-type-options"] == "nosniff"
            assert health.headers["x-frame-options"] == "DENY"
            assert "default-src 'self'" in health.headers["content-security-policy"]
            assert health.headers["strict-transport-security"].startswith("max-age=")
            login = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "correct-password"},
            )
            assert login.status_code == 200
            blocked = await client.post(
                "/api/auth/logout",
                headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
            )
            assert blocked.status_code == 403
