"""FastAPI application for the web UI.

Mounts the JSON API + SSE stream and serves the built React SPA (``web/dist``)
when available. In development the Vite dev server proxies ``/api`` here.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..config import Config
from .auth import COOKIE_NAME
from .deps import WebContext, get_context, require_user
from .routers import account, auth
from .routers import config as config_router
from .routers import jobs, manager, metrics
from .sse import event_stream

logger = logging.getLogger(__name__)

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
ASSETS_DIR = FRONTEND_DIST / "assets"


def create_app(config: Optional[Config] = None) -> FastAPI:
    explicit_config = config is not None
    cfg = config or Config()

    async def initialize_context(app: FastAPI) -> None:
        delay = 0.5
        while getattr(app.state, "web_context", None) is None:
            try:
                ctx = await asyncio.to_thread(WebContext, cfg)
                app.state.web_context = ctx
                app.state.context_error = None
                await ctx.runner.start_monitor()
                logger.info("Web context initialized (%s)", ctx.database_source)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                app.state.context_error = type(exc).__name__
                logger.warning(
                    "Web context initialization failed (%s); retrying",
                    type(exc).__name__,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10.0)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.web_context = None
        app.state.context_error = None
        app.state.context_task = None

        if explicit_config:
            ctx = WebContext(cfg)
            app.state.web_context = ctx
            await ctx.runner.start_monitor()
        else:
            # Production fast-start: let Uvicorn serve / immediately while
            # database initialization/recovery happens in the background.
            app.state.context_task = asyncio.create_task(initialize_context(app))

        yield

        task = getattr(app.state, "context_task", None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        ctx = getattr(app.state, "web_context", None)
        if ctx is not None:
            await ctx.runner.shutdown()
            await ctx.account.shutdown_async()
            ctx.close()

    app = FastAPI(
        title="telegram-phone-number-checker Web UI",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def browser_security(request: Request, call_next):
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and request.cookies.get(COOKIE_NAME):
            fetch_site = request.headers.get("sec-fetch-site", "").lower()
            origin = request.headers.get("origin")
            forwarded_host = request.headers.get("x-forwarded-host")
            request_host = (forwarded_host or request.headers.get("host", "")).split(",", 1)[0].strip().lower()
            origin_host = urlsplit(origin).netloc.lower() if origin else ""
            if fetch_site == "cross-site" or (origin_host and request_host and origin_host != request_host):
                return JSONResponse({"detail": "Cross-site request rejected."}, status_code=403)

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
        if request.url.scheme == "https" or forwarded_proto == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    app.include_router(auth.router)
    app.include_router(jobs.router)
    app.include_router(account.router)
    app.include_router(config_router.router)
    app.include_router(manager.router)
    app.include_router(metrics.router)

    @app.get("/api/events", include_in_schema=False)
    async def events(
        request: Request, _user: str = Depends(require_user)
    ) -> StreamingResponse:
        ctx = get_context(request)
        return StreamingResponse(
            event_stream(ctx.sse),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/ready", include_in_schema=False, response_model=None)
    async def ready(request: Request):
        frontend_ok = (FRONTEND_DIST / "index.html").exists() and ASSETS_DIR.exists()
        ctx = getattr(request.app.state, "web_context", None)
        if ctx is None:
            return JSONResponse(
                {
                    "status": "starting",
                    "database": False,
                    "database_backend": "PostgreSQL",
                    "startup_error": getattr(request.app.state, "context_error", None),
                    "frontend": frontend_ok,
                },
                status_code=503,
            )

        database_ok = False
        try:
            row = ctx.db.execute("SELECT 1 AS ok").fetchone()
            database_ok = bool(row and row["ok"] == 1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Readiness database check failed: %s", type(exc).__name__)
        payload = {
            "status": "ready" if database_ok and frontend_ok else "not_ready",
            "database": database_ok,
            "database_backend": "PostgreSQL" if getattr(ctx.db, "_use_postgres", False) else "SQLite",
            "database_source": getattr(ctx, "database_source", "unknown"),
            "database_fingerprint": getattr(ctx, "database_fingerprint", "unknown"),
            "frontend": frontend_ok,
        }
        return JSONResponse(payload, status_code=200 if database_ok and frontend_ok else 503)

    if ASSETS_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            index = FRONTEND_DIST / "index.html"
            if index.exists():
                return FileResponse(index)
            return JSONResponse({"detail": "index.html not found"}, status_code=404)

    else:

        @app.get("/", include_in_schema=False, response_model=None)
        async def no_frontend() -> JSONResponse:
            return JSONResponse(
                {
                    "detail": (
                        "Web UI chưa được build. Chạy 'npm install && npm run build' "
                        "trong thư mục web/ rồi khởi động lại."
                    )
                },
                status_code=404,
            )

        @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
        async def api_only(full_path: str) -> JSONResponse:
            if full_path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            return JSONResponse(
                {"detail": "Web UI chưa được build. Xem mục README."}, status_code=404
            )

    return app


web = create_app()
