import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import secrets_store, session_store
from .auth import _client_ip, require_auth
from .auth import router as auth_router
from .config import settings
from .db import AsyncSessionLocal, check_db, init_db
from .models import AuditLog
from .routers import accounts, bulk, groups, messaging, profile, security
from .routers import settings as settings_router
from .tg_manager import manager

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # validate critical env
    if not settings.APP_PASSWORD:
        log.warning("APP_PASSWORD is empty — set it in backend/.env!")
    if not settings.SESSION_SECRET or len(settings.SESSION_SECRET) < 16:
        log.warning("SESSION_SECRET is missing or too short — set it in backend/.env!")
    await init_db()
    await settings_router.load_runtime_settings()
    await secrets_store.migrate_legacy_store()
    manager.set_loop(asyncio.get_event_loop())
    await manager.startup_load_all()

    async def status_loop():
        while True:
            try:
                await manager.refresh_status_all()
            except Exception as e:
                log.warning("status refresh: %s", e)
            await asyncio.sleep(30)

    task = asyncio.create_task(status_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await manager.shutdown()


app = FastAPI(title="Multi TG Manager", lifespan=lifespan)


@app.middleware("http")
async def audit_mutations(request: Request, call_next):
    response = await call_next(request)
    if request.method in {
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    } and request.url.path.startswith("/api/"):
        try:
            async with AsyncSessionLocal() as db:
                db.add(
                    AuditLog(
                        method=request.method,
                        path=request.url.path[:255],
                        status_code=response.status_code,
                        client_ip=_client_ip(request)[:64],
                    )
                )
                await db.commit()
        except Exception as exc:
            log.warning("audit log write failed: %s", exc)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.ALLOWED_ORIGIN, "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# auth endpoints (public)
app.include_router(auth_router)


@app.get("/api/health")
async def health():
    telegram_configured = bool(settings.TG_API_ID and settings.TG_API_HASH)
    return {
        "ok": True,
        "api_id_set": bool(settings.TG_API_ID),
        "telegram_configured": telegram_configured,
        "clients": len(manager._clients),
    }


@app.get("/api/readiness")
async def readiness():
    reasons = []
    telegram_configured = bool(settings.TG_API_ID and settings.TG_API_HASH)
    if not (settings.APP_PASSWORD and settings.SESSION_SECRET):
        reasons.append("app_auth")
    if not await check_db():
        reasons.append("database")
    else:
        try:
            if not await session_store.all_accounts_backed_up():
                reasons.append("portable_session_store")
        except Exception:
            reasons.append("portable_session_store")
    ready = not reasons
    return JSONResponse(
        {
            "ready": ready,
            "reasons": reasons,
            "telegram_configured": telegram_configured,
        },
        status_code=200 if ready else 503,
    )


# all data routers require auth
PROTECTED_DEPS = [Depends(require_auth)]
app.include_router(accounts.router, dependencies=PROTECTED_DEPS)
app.include_router(profile.router, dependencies=PROTECTED_DEPS)
app.include_router(security.router, dependencies=PROTECTED_DEPS)
app.include_router(groups.router, dependencies=PROTECTED_DEPS)
app.include_router(messaging.router, dependencies=PROTECTED_DEPS)
app.include_router(settings_router.router, dependencies=PROTECTED_DEPS)
app.include_router(bulk.router, dependencies=PROTECTED_DEPS)


# Any /api/* path that didn't match a real route above returns a clean JSON 404
# for ANY method. Registered before the GET-only SPA fallback so an unmatched
# POST/PUT/DELETE (e.g. calling a new endpoint against a stale server) surfaces a
# proper "Not Found" instead of a confusing "405 Method Not Allowed".
@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def api_not_found(rest: str):
    return JSONResponse({"detail": "Not Found"}, status_code=404)


# ---- serve built frontend (single-port mode) ----
# `start.bat` builds the frontend into backend/static/. If that folder exists, serve it.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _safe_static_candidate(root: Path, raw_path: str) -> Path | None:
    """Resolve a requested static path without allowing traversal outside root."""
    decoded = raw_path or ""
    for _ in range(3):
        newer = unquote(decoded)
        if newer == decoded:
            break
        decoded = newer
    decoded = decoded.replace("\\", "/")
    if "\x00" in decoded or Path(decoded).is_absolute():
        return None
    root_resolved = root.resolve()
    candidate = (root_resolved / decoded).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    return candidate


if STATIC_DIR.is_dir():
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str, request: Request):
        # never intercept the api
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        # try a real file first (favicon, etc.) but never leave STATIC_DIR
        candidate = _safe_static_candidate(STATIC_DIR, full_path)
        if candidate is None:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if candidate.is_file():
            return FileResponse(str(candidate))
        index = STATIC_DIR / "index.html"
        if index.is_file():
            return FileResponse(str(index))
        return JSONResponse(
            {"detail": "Frontend not built. Run start.bat."}, status_code=503
        )
else:

    @app.get("/")
    async def no_static():
        return JSONResponse(
            {
                "detail": "Frontend not built. Run `npm run build` in frontend/ or use start.bat."
            },
            status_code=503,
        )
