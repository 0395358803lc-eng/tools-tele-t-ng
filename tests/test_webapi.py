"""HTTP-level tests for the web UI (FastAPI app + routers)."""

import asyncio
import os

import httpx
import pytest

from telegram_phone_number_checker.config import Config
from telegram_phone_number_checker.webapi.app import create_app
from telegram_phone_number_checker.webapi.auth import COOKIE_NAME

TEST_USERNAME = "testadmin"
TEST_PASSWORD = "testpass123"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_UI_USERNAME", TEST_USERNAME)
    monkeypatch.setenv("WEB_UI_PASSWORD", TEST_PASSWORD)
    monkeypatch.setenv("WEB_UI_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("WEB_UI_COOKIE_SECURE", "false")
    cfg = Config()
    cfg.api_id = None
    cfg.api_hash = None
    cfg.api_phone_number = None
    cfg.proxy = None
    cfg.database_url = None
    cfg.database_path = tmp_path / "webapi.db"
    return create_app(cfg)


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        async with app.router.lifespan_context(app):
            yield c


@pytest.fixture
def web_context(app):
    return app.state.web_context


@pytest.fixture
async def authed(client):
    r = await client.post(
        "/api/auth/login",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert r.status_code == 200
    return client


async def test_health(app, client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_api_requires_auth(app, client):
    for path in ("/api/jobs", "/api/account/status", "/api/config", "/api/events"):
        r = await client.get(path)
        assert r.status_code == 401, path
        assert "web_session" not in r.headers.get("set-cookie", ""), path


async def test_bad_login_rejected(app, client):
    r = await client.post(
        "/api/auth/login",
        json={"username": TEST_USERNAME, "password": "wrong"},
    )
    assert r.status_code == 401


async def test_login_and_me(app, authed):
    r = await authed.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["username"] == TEST_USERNAME


async def test_logout_clears_cookie(app, authed):
    r = await authed.post("/api/auth/logout")
    assert r.status_code == 200
    # after logout the cookie should be cleared -> still needs web_session
    assert "web_session=" in r.headers.get("set-cookie", "")


async def test_get_missing_job_404(app, authed):
    r = await authed.get("/api/jobs/nope")
    assert r.status_code == 404


async def test_create_job_and_list(app, authed):
    r = await authed.post(
        "/api/jobs",
        json={"phone_numbers": "+84911111111, +84922222222", "job_name": "My job"},
    )
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["name"] == "My job"
    assert detail["total"] == 2
    assert detail["status"] in ("CREATED", "PAUSED", "RUNNING", "COMPLETED")

    r = await authed.get("/api/jobs")
    assert r.status_code == 200
    listing = r.json()
    assert listing["total"] == 1
    assert listing["items"][0]["job_id"] == detail["job_id"]


async def test_create_job_rejects_invalid_number(app, authed):
    r = await authed.post(
        "/api/jobs",
        json={"phone_numbers": "+84911111111, definitely-not-a-number"},
    )
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["total"] == 2
    # one number is syntactically invalid -> permanent error registered
    assert detail["errors"] == 1


async def test_create_job_empty_rejected(app, authed):
    r = await authed.post("/api/jobs", json={"phone_numbers": "  "})
    assert r.status_code == 422


async def test_items_masked_and_filtered(app, authed):
    r = await authed.post(
        "/api/jobs",
        json={"phone_numbers": "+84911111111, +84922222222"},
    )
    job_id = r.json()["job_id"]

    r = await authed.get(f"/api/jobs/{job_id}/items")
    assert r.status_code == 200
    items = r.json()
    assert items["total"] == 2

    for item in items["items"]:
        assert item["masked_phone"] not in ("+84911111111", "+84922222222")
        assert "original_phone" not in item
        assert "normalized_phone" not in item

    r = await authed.get(f"/api/jobs/{job_id}/items", params={"q": "491111"})
    assert r.status_code == 200
    assert r.json()["total"] == 1


async def test_export_json_and_csv(app, authed):
    r = await authed.post(
        "/api/jobs", json={"phone_numbers": "+84911111111, +84922222222"}
    )
    job_id = r.json()["job_id"]

    r = await authed.get(f"/api/jobs/{job_id}/export", params={"format": "json"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert isinstance(body, list) or isinstance(body, dict)

    r = await authed.get(f"/api/jobs/{job_id}/export", params={"format": "csv"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")


async def test_export_missing_job_404(app, authed):
    r = await authed.get("/api/jobs/nope/export")
    assert r.status_code == 404


async def test_pause_job(app, authed):
    r = await authed.post(
        "/api/jobs", json={"phone_numbers": "+84911111111", "auto_start": False}
    )
    job_id = r.json()["job_id"]

    r = await authed.post(f"/api/jobs/{job_id}/pause")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


async def test_delete_job(app, authed):
    r = await authed.post(
        "/api/jobs", json={"phone_numbers": "+84911111111", "auto_start": False}
    )
    job_id = r.json()["job_id"]

    r = await authed.delete(f"/api/jobs/{job_id}")
    assert r.status_code == 200

    r = await authed.get(f"/api/jobs/{job_id}")
    assert r.status_code == 404


async def test_delete_active_job_conflict(app, authed, web_context):
    r = await authed.post(
        "/api/jobs", json={"phone_numbers": "+84911111111", "auto_start": False}
    )
    job_id = r.json()["job_id"]

    # simulate an active job that stays running until cancelled
    async def keep_running(job_id, auto_resume=None):
        try:
            await asyncio.Event().wait()
        finally:
            await web_context.runner._publish_job(job_id, "job_update")

    original = web_context.runner._run_job
    web_context.runner._run_job = keep_running  # type: ignore[method-assign]
    web_context.runner.start(job_id)
    try:
        assert web_context.runner.is_active(job_id)

        r = await authed.delete(f"/api/jobs/{job_id}")
        assert r.status_code == 409
    finally:
        web_context.runner._run_job = original  # type: ignore[method-assign]
        await web_context.runner.shutdown()


async def test_import_job(app, authed):
    files = {"file": ("phones.csv", b"phone\n+84911111111\n+84922222222\n")}
    r = await authed.post("/api/jobs/import", files=files)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2


async def test_import_empty_file_rejected(app, authed):
    files = {"file": ("empty.csv", b"")}
    r = await authed.post("/api/jobs/import", files=files)
    assert r.status_code == 422


async def test_account_status_not_configured(app, authed):
    r = await authed.get("/api/account/status")
    assert r.status_code == 200
    assert r.json()["state"] == "NOT_CONFIGURED"


async def test_account_logout_unconfigured(app, authed):
    r = await authed.post("/api/account/logout")
    assert r.status_code == 400


async def test_config_hides_secrets(app, authed, web_context, tmp_path):
    cfg = web_context.config
    cfg.api_id = "12345678"
    cfg.api_hash = "supersecrethash"
    cfg.api_phone_number = "+84911111111"

    r = await authed.get("/api/config")
    assert r.status_code == 200
    data = r.json()
    assert data["api_id_masked"] == "12****78"
    assert data["api_hash_set"] is True
    assert "supersecrethash" not in r.text
    assert data["api_phone_number"] == "+8491111****1111"
    assert data["web_ui"]["username"] == TEST_USERNAME


async def test_unknown_api_route_404_json(app, authed):
    r = await authed.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


async def test_spa_serves_index(app, authed):
    from telegram_phone_number_checker.webapi.app import ASSETS_DIR

    if not ASSETS_DIR.exists():
        pytest.skip("web/dist not built; skipping SPA assert")
    r = await authed.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert '<div id="root"></div>' in r.text

    r = await authed.get("/some/spa/route")
    assert r.status_code == 200


async def test_events_requires_auth(app, client):
    r = await client.get("/api/events")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_runner_monitor_publishes_summary(app, authed, web_context):
    """The monitor loop publishes a well-formed job_update event for active jobs."""
    import asyncio

    ctx = web_context
    events = []

    async def on_event(message):
        events.append(message)

    original_publish = ctx.sse.publish

    ctx.sse.publish = on_event  # type: ignore[method-assign]

    r = await authed.post(
        "/api/jobs", json={"phone_numbers": "+84911111111", "auto_start": False}
    )
    job_id = r.json()["job_id"]
    ctx.runner.start(job_id)
    try:
        await asyncio.sleep(2.2)
    finally:
        await ctx.runner.shutdown()

    ctx.sse.publish = original_publish  # type: ignore[method-assign]

    updates = [
        e for e in events if e.get("type") == "job_update" and e.get("job_id") == job_id
    ]
    assert updates, "expected at least one job_update event"
    last = updates[-1]
    assert last["data"]["name"] is not None
    assert last["data"]["status"] in (
        "CREATED",
        "PAUSED",
        "COMPLETED",
        "RUNNING",
        "FAILED",
    )


async def test_create_job_accepts_newline_separated_numbers(app, authed):
    r = await authed.post(
        "/api/jobs",
        json={"phone_numbers": "+84911111111\n+84922222222", "job_name": "newline"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2


async def test_import_csv_uses_phone_column_and_multipart_job_name(app, authed):
    files = {
        "file": (
            "phones.csv",
            b"name,phone\nAlice,+84911111111\nBob,+84922222222\n",
            "text/csv",
        )
    }
    r = await authed.post(
        "/api/jobs/import", files=files, data={"job_name": "csv-named-job"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2
    assert r.json()["name"] == "csv-named-job"


async def test_cancel_inactive_job_marks_cancelled(app, authed):
    created = await authed.post(
        "/api/jobs",
        json={"phone_numbers": "+84911111111", "auto_start": False},
    )
    job_id = created.json()["job_id"]

    cancelled = await authed.post(f"/api/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200

    detail = await authed.get(f"/api/jobs/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "CANCELLED"
    assert detail.json()["finished_at"] is not None


async def test_import_rejects_unsupported_extension(app, authed):
    files = {"file": ("phones.json", b"[]", "application/json")}
    r = await authed.post("/api/jobs/import", files=files)
    assert r.status_code == 415


async def test_import_rejects_oversized_file(app, authed, monkeypatch):
    from telegram_phone_number_checker.webapi.routers import jobs as jobs_router

    monkeypatch.setattr(jobs_router, "MAX_UPLOAD_BYTES", 32)
    payload = b"phone\n" + (b"+84911111111\n" * 5)
    files = {"file": ("phones.csv", payload, "text/csv")}
    r = await authed.post("/api/jobs/import", files=files)
    assert r.status_code == 413


async def test_csv_bom_crlf_quotes_and_duplicates(app, authed):
    payload = (
        b'\xef\xbb\xbfname,phone\r\n'
        b'"Alice","+84911111111"\r\n'
        b'"Bob","+84911111111"\r\n'
        b'"Carol","+84922222222"\r\n'
    )
    files = {"file": ("phones.csv", payload, "text/csv")}
    r = await authed.post("/api/jobs/import", files=files, data={"job_name": "CSV edge"})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2
    assert r.json()["name"] == "CSV edge"


async def test_csv_invalid_number_becomes_terminal_error(app, authed):
    payload = b"phone\n+84911111111\ndefinitely-not-a-number\n"
    files = {"file": ("phones.csv", payload, "text/csv")}
    r = await authed.post("/api/jobs/import", files=files)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2
    assert r.json()["processed"] == 1
    assert r.json()["errors"] == 1


async def test_items_response_never_contains_raw_phone(app, authed):
    phone = "+84911111111"
    created = await authed.post(
        "/api/jobs", json={"phone_numbers": phone, "auto_start": False}
    )
    job_id = created.json()["job_id"]
    r = await authed.get(f"/api/jobs/{job_id}/items")
    assert r.status_code == 200
    assert phone not in r.text
    item = r.json()["items"][0]
    assert "original_phone" not in item
    assert "normalized_phone" not in item
    assert "masked_phone" in item


async def test_large_job_pagination_caps_page_size(app, authed, web_context):
    from telegram_phone_number_checker.repositories.job_repository import JobRepository
    from telegram_phone_number_checker.repositories.result_repository import ResultRepository

    job_id = "pagination-large"
    JobRepository(web_context.db).create(job_id, name="large", total_items=1001)
    items = ResultRepository(web_context.db)
    for i in range(1001):
        phone = f"+1000{i:04d}"
        items.insert(job_id, phone, phone, 3)

    first = await authed.get(f"/api/jobs/{job_id}/items?page=1&page_size=999")
    assert first.status_code == 200
    assert first.json()["total"] == 1001
    assert first.json()["page_size"] == 200
    assert len(first.json()["items"]) == 200
    last = await authed.get(f"/api/jobs/{job_id}/items?page=6&page_size=200")
    assert len(last.json()["items"]) == 1


async def test_job_detail_exposes_retry_and_floodwait_times(app, authed, web_context):
    from telegram_phone_number_checker.rate_limiter import account_key_from_phone
    from telegram_phone_number_checker.models import now_iso

    web_context.config.api_phone_number = "+84999999999"
    created = await authed.post(
        "/api/jobs", json={"phone_numbers": "+84911111111", "auto_start": False}
    )
    job_id = created.json()["job_id"]
    retry_at = "2099-01-01T00:00:00+00:00"
    web_context.db.execute(
        "UPDATE check_items SET status='RETRY_REQUIRED', next_retry_at=? WHERE job_id=?",
        (retry_at, job_id),
    )
    key = account_key_from_phone(web_context.config.api_phone_number)
    web_context.db.execute(
        "INSERT INTO account_runtime_state(account_key,blocked_until,updated_at) VALUES(?,?,?)",
        (key, retry_at, now_iso()),
    )
    web_context.db.commit()
    detail = await authed.get(f"/api/jobs/{job_id}")
    assert detail.json()["next_retry_at"] == retry_at
    assert detail.json()["account_blocked_until"] == retry_at


async def test_readiness_checks_database_and_frontend(app, authed):
    r = await authed.get("/api/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["database"] is True
    assert body["frontend"] is True
    assert body["database_backend"] == "SQLite"


async def test_multi_account_list_default_and_job_binding(app, authed, web_context):
    a1 = await authed.post("/api/account/accounts", json={"phone": "+84911111111", "label": "Account 1"})
    a2 = await authed.post("/api/account/accounts", json={"phone": "+84922222222", "label": "Account 2"})
    assert a1.status_code == 200 and a2.status_code == 200
    id1, id2 = a1.json()["id"], a2.json()["id"]
    listing = (await authed.get("/api/account/accounts")).json()
    assert listing["total"] == 2
    assert next(x for x in listing["items"] if x["id"] == id1)["is_default"] is True

    r = await authed.post(f"/api/account/accounts/{id2}/default")
    assert r.status_code == 200
    created = await authed.post("/api/jobs", json={"phone_numbers": "+84933333333", "telegram_account_id": id2})
    assert created.status_code == 200, created.text
    job = web_context.runner._config_for_job(created.json()["job_id"])
    assert job.api_phone_number == "+84922222222"


async def test_multi_account_delete_and_default_replacement(app, authed):
    a1 = (await authed.post("/api/account/accounts", json={"phone": "+84911111111"})).json()["id"]
    a2 = (await authed.post("/api/account/accounts", json={"phone": "+84922222222"})).json()["id"]
    assert (await authed.delete(f"/api/account/accounts/{a1}")).status_code == 200
    listing = (await authed.get("/api/account/accounts")).json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == a2
    assert listing["items"][0]["is_default"] is True


async def test_create_parallel_multi_account_job_with_separate_datasets(app, authed, web_context):
    a1 = (await authed.post("/api/account/accounts", json={"phone": "+84911111111", "label": "A"})).json()["id"]
    a2 = (await authed.post("/api/account/accounts", json={"phone": "+84922222222", "label": "B"})).json()["id"]
    r = await authed.post("/api/jobs", json={
        "mode": "MULTI", "job_name": "Parallel",
        "account_batches": [
            {"telegram_account_id": a1, "phone_numbers": "+84933333333\n+84944444444"},
            {"telegram_account_id": a2, "phone_numbers": "+84955555555"},
        ],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_mode"] == "MULTI"
    assert body["total"] == 3
    assert len(body["branches"]) == 2
    from telegram_phone_number_checker.repositories.job_repository import JobRepository
    from telegram_phone_number_checker.repositories.result_repository import ResultRepository
    repo = JobRepository(web_context.db)
    children = repo.list_children(body["job_id"])
    counts = {c.telegram_account_phone: ResultRepository(web_context.db).count(c.id) for c in children}
    assert counts == {"+84911111111": 2, "+84922222222": 1}
    items = (await authed.get(f"/api/jobs/{body['job_id']}/items?page=1&page_size=10")).json()
    assert items["total"] == 3
    assert all(item["telegram_account_phone"] for item in items["items"])
    listing = (await authed.get("/api/jobs")).json()
    assert listing["total"] == 1
    assert listing["items"][0]["job_id"] == body["job_id"]


async def test_explicit_account_auto_start_requires_authorized(app, authed):
    added = await authed.post(
        "/api/account/accounts", json={"phone": "+84911111111", "label": "Not logged in"}
    )
    account_id = added.json()["id"]
    r = await authed.post("/api/jobs", json={
        "phone_numbers": "+84922222222", "telegram_account_id": account_id,
        "auto_start": True,
    })
    assert r.status_code == 409


async def test_account_logout_blocked_while_unfinished_job_exists(app, authed):
    added = await authed.post(
        "/api/account/accounts", json={"phone": "+84911111111", "label": "Bound"}
    )
    account_id = added.json()["id"]
    created = await authed.post("/api/jobs", json={
        "phone_numbers": "+84922222222", "telegram_account_id": account_id,
        "auto_start": False,
    })
    assert created.status_code == 200
    r = await authed.post(f"/api/account/accounts/{account_id}/logout")
    assert r.status_code == 409

async def test_two_independent_clients_share_server_persisted_data(app):
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://client-a") as a, \
                   httpx.AsyncClient(transport=transport, base_url="http://client-b") as b:
            for c in (a, b):
                r = await c.post("/api/auth/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
                assert r.status_code == 200
            created = await a.post("/api/jobs", json={
                "phone_numbers": "+84911111111", "job_name": "cross-client"
            })
            assert created.status_code == 200
            job_id = created.json()["job_id"]
            jobs_b = await b.get("/api/jobs")
            assert jobs_b.status_code == 200
            assert any(j["job_id"] == job_id for j in jobs_b.json()["items"])
            account = await a.post("/api/account/accounts", json={
                "phone": "+84922222222", "label": "Shared account"
            })
            assert account.status_code == 200
            accounts_b = await b.get("/api/account/accounts")
            assert accounts_b.status_code == 200
            assert any(x["id"] == account.json()["id"] for x in accounts_b.json()["items"])


async def test_session_upload_tempfile_is_removed(app, authed, web_context, monkeypatch):
    captured = {}
    async def fake_import(path, label=None, account_id=None):
        captured["path"] = path
        assert path.exists()
        return {"id": "a1", "label": label or "Imported", "phone": "+84******678", "is_default": True, "state": "AUTHORIZED", "last_login_at": None, "login": None}
    monkeypatch.setattr(web_context.account, "import_session_file", fake_import)
    payload = b"SQLite format 3\x00" + b"x" * 256
    r = await authed.post(
        "/api/account/session/import",
        files={"file": ("owned.session", payload, "application/x-sqlite3")},
        data={"label": "Imported"},
    )
    assert r.status_code == 200, r.text
    assert captured["path"].exists() is False

async def test_manager_api_requires_auth(client):
    r = await client.get("/api/manager/profile/test-account")
    assert r.status_code == 401


async def test_manager_profile_security_groups_and_message_routes(authed, web_context):
    class FakeManagerService:
        async def get_profile(self):
            return {"id": 7, "phone": "84900000000", "first_name": "Demo", "last_name": "User", "username": "demo", "about": "bio", "premium": False, "verified": False}
        async def update_profile(self, first_name, last_name="", about=""):
            return {"first_name": first_name, "last_name": last_name, "username": "demo"}
        async def update_username(self, username):
            return {"username": username}
        async def get_authorizations(self):
            return [{"hash": "123", "current": True, "device_model": "Web", "platform": "Linux", "system_version": "", "app_name": "Telegram", "app_version": "", "ip": "127.0.0.1", "country": "", "region": "", "date_active": None}]
        async def security_messages(self, limit=50):
            return [{"id": 1, "text": "Security notice", "date": None}]
        async def list_dialogs(self, limit=100):
            return [{"id": "99", "title": "Group", "username": "group", "is_group": True, "is_channel": False, "unread_count": 0}]
        async def join_chat(self, target):
            return {"id": "99", "title": "Group", "username": target.lstrip("@"), "is_group": True, "is_channel": False, "unread_count": 0}
        async def leave_chat(self, target):
            return True
        async def send_text(self, target, text):
            return {"id": 42, "date": None}
        async def terminate_authorization(self, hash_id):
            return True

    original = web_context.account.management_service
    web_context.account.management_service = lambda account_id: FakeManagerService()
    try:
        r = await authed.get("/api/manager/profile/a1")
        assert r.status_code == 200 and r.json()["username"] == "demo"
        r = await authed.put("/api/manager/profile/a1", json={"first_name": "New", "last_name": "Name", "about": "About"})
        assert r.status_code == 200 and r.json()["first_name"] == "New"
        r = await authed.put("/api/manager/profile/a1/username", json={"username": "newname"})
        assert r.status_code == 200 and r.json()["username"] == "newname"
        r = await authed.get("/api/manager/security/a1/sessions")
        assert r.status_code == 200 and len(r.json()["items"]) == 1
        r = await authed.get("/api/manager/security/a1/messages")
        assert r.status_code == 200 and r.json()["items"][0]["text"] == "Security notice"
        r = await authed.get("/api/manager/groups/a1")
        assert r.status_code == 200 and r.json()["items"][0]["title"] == "Group"
        r = await authed.post("/api/manager/groups/a1/join", json={"target": "@group"})
        assert r.status_code == 200
        r = await authed.post("/api/manager/groups/a1/leave", json={"target": "@group"})
        assert r.status_code == 200 and r.json()["ok"] is True
        r = await authed.post("/api/manager/messages/a1/send", json={"target": "@user", "text": "hello"})
        assert r.status_code == 200 and r.json()["id"] == 42
    finally:
        web_context.account.management_service = original


async def test_manager_conflict_when_account_is_busy(authed, web_context):
    from telegram_phone_number_checker.webapi.account_manager import AccountAlreadyBusy
    original = web_context.account.management_service
    def busy(_account_id):
        raise AccountAlreadyBusy("busy")
    web_context.account.management_service = busy
    try:
        r = await authed.get("/api/manager/profile/a1")
        assert r.status_code == 409
    finally:
        web_context.account.management_service = original


async def test_manager_deep_capability_routes(authed, web_context):
    class DeepFake:
        async def check_username(self, username): return {"available": username == "free_name", "reason": None}
        async def upload_profile_photo(self, file_path):
            from pathlib import Path
            assert Path(file_path).exists()
            return True
        async def download_profile_photo(self): return b"\xff\xd8fakejpegdata\xff\xd9"
        async def terminate_other_authorizations(self): return {"terminated": 2, "failed": 0}
        async def open_chat(self, raw, limit=40):
            return {"peer": {"id": "7", "ref": "demo", "title": "Demo", "username": "demo", "kind": "user", "is_bot": False}, "started": False, "start_param": None, "messages": []}
        async def chat_history(self, peer, limit=40): return [{"id": 1, "out": False, "text": "hi", "media": None, "date": None, "service": False, "sender_id": "7"}]
        async def chat_send(self, peer, text): return {"id": 2, "out": True, "text": text, "media": None, "date": None, "service": False, "sender_id": None}
        async def target_usage(self, target): return {"status": "present", "detail": "Có lịch sử tin nhắn", "peer": {"id": "7", "title": "Demo"}}
        async def available_reactions(self): return ["👍", "❤️"]
        async def react_post(self, post_link, emoji, custom_emoji_id=None): return {"ok": True, "message_id": 9}
        async def view_post(self, post_link): return {"ok": True, "message_id": 9, "views": 12}

    original_service = web_context.account.management_service
    original_list = web_context.account.list_accounts
    web_context.account.management_service = lambda _account_id: DeepFake()
    async def fake_list_accounts():
        return {"items": [{"id": "a1", "label": "Demo", "phone": "+84***", "is_default": True, "state": "AUTHORIZED", "last_login_at": None, "login": None, "session_backend": "SQL"}], "total": 1}
    web_context.account.list_accounts = fake_list_accounts
    try:
        r = await authed.get("/api/manager/profile/a1/username-check", params={"username": "free_name"})
        assert r.status_code == 200 and r.json()["available"] is True
        r = await authed.post("/api/manager/profile/a1/photo", files={"file": ("avatar.jpg", b"0123456789abcdef012345", "image/jpeg")})
        assert r.status_code == 200 and r.json()["ok"] is True
        r = await authed.get("/api/manager/profile/a1/photo")
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/jpeg")
        r = await authed.post("/api/manager/security/a1/sessions/terminate-others")
        assert r.status_code == 200 and r.json()["terminated"] == 2
        r = await authed.post("/api/manager/messages/a1/open", json={"input": "@demo", "limit": 20})
        assert r.status_code == 200 and r.json()["peer"]["title"] == "Demo"
        r = await authed.get("/api/manager/messages/a1/history", params={"peer": "demo"})
        assert r.status_code == 200 and r.json()["messages"][0]["text"] == "hi"
        r = await authed.post("/api/manager/messages/a1/chat-send", json={"peer": "demo", "text": "hello"})
        assert r.status_code == 200 and r.json()["message"]["text"] == "hello"
        r = await authed.post("/api/manager/target-check", json={"target": "@demo", "account_ids": ["a1"]})
        assert r.status_code == 200 and len(r.json()["present"]) == 1
        r = await authed.get("/api/manager/posts/a1/reactions")
        assert r.status_code == 200 and "👍" in r.json()["items"]
        r = await authed.post("/api/manager/posts/a1/react", json={"post_link": "https://t.me/demo/9", "emoji": "👍"})
        assert r.status_code == 200 and r.json()["message_id"] == 9
        r = await authed.post("/api/manager/posts/a1/view", json={"post_link": "https://t.me/demo/9"})
        assert r.status_code == 200 and r.json()["views"] == 12
        actions = {row["action"] for row in web_context.manager_store.recent_audit(50)}
        assert "profile.photo" in actions
        assert "security.sessions.terminate_others" in actions
        assert "messages.chat_send" in actions
        assert "posts.react" in actions
        assert "posts.view" in actions
    finally:
        web_context.account.management_service = original_service
        web_context.account.list_accounts = original_list


async def test_manager_bulk_admin_routes(authed, web_context):
    class FakeBulkService:
        async def join_chat(self, target):
            return {"id": "1", "title": target, "username": ""}
        async def leave_chat(self, target):
            return True
        async def terminate_other_authorizations(self):
            return {"terminated": 2, "failed": 0}

    original = web_context.account.management_service
    web_context.account.management_service = lambda _account_id: FakeBulkService()
    try:
        r = await authed.post("/api/manager/bulk/join", json={"account_ids": ["a1", "a2"], "target": "@group"})
        assert r.status_code == 200 and r.json()["succeeded"] == 2
        r = await authed.post("/api/manager/bulk/leave", json={"account_ids": ["a1"], "target": "@group"})
        assert r.status_code == 200 and r.json()["failed"] == 0
        r = await authed.post("/api/manager/bulk/terminate-others", json={"account_ids": ["a1", "a2"]})
        assert r.status_code == 200 and r.json()["succeeded"] == 2
    finally:
        web_context.account.management_service = original


async def test_import_xlsx_supported(app, authed):
    import io
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(["name", "phone"])
    sheet.append(["Alice", "+84911111111"])
    sheet.append(["Bob", "+84922222222"])
    buffer = io.BytesIO()
    book.save(buffer)
    files = {"file": ("phones.xlsx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    response = await authed.post("/api/jobs/import", files=files)
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 2


async def test_distributed_import_deduplicates_globally(app, authed, web_context):
    from telegram_phone_number_checker.repositories.account_repository import AccountRepository

    repo = AccountRepository(web_context.db)
    first = repo.create("dist-a", "+84987654321", "Account A")
    second = repo.create("dist-b", "+84976543210", "Account B")
    payload = b"phone\n+84911111111\n0911111111\n+84922222222\n0922222222\n"
    files = {"file": ("phones.csv", payload, "text/csv")}
    data = {"telegram_account_ids": f"{first['id']},{second['id']}", "job_name": "distributed"}
    response = await authed.post("/api/jobs/import", files=files, data=data)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_mode"] == "MULTI"
    assert body["total"] == 2
    assert len(body["branches"]) == 2
    assert sorted(branch["total"] for branch in body["branches"]) == [1, 1]


async def test_metrics_requires_auth(app, client):
    response = await client.get("/api/metrics")
    assert response.status_code == 401


async def test_metrics_exposes_only_operational_counts(app, authed):
    created = await authed.post(
        "/api/jobs", json={"phone_numbers": "+84911111111", "auto_start": False}
    )
    assert created.status_code == 200
    response = await authed.get("/api/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["jobs_total"] >= 1
    assert body["items_total"] >= 1
    assert "jobs_by_status" in body and "items_by_status" in body
    assert "database_backend" in body
    assert "+84911111111" not in response.text


async def test_manager_audit_export_records_event(authed, web_context):
    web_context.manager_store.audit("profile.update", "acct-1", "ok", "done")
    response = await authed.get("/api/manager/audit/export?max_rows=100")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "profile.update" in response.text
    assert "done" in response.text
    actions = [row["action"] for row in web_context.manager_store.recent_audit(20)]
    assert "audit.export" in actions
