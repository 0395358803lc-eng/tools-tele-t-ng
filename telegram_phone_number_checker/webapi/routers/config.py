"""Read-only runtime config view (secrets never exposed)."""

import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ...models import mask_phone
from ..auth import TOKEN_MAX_AGE_SECONDS
from ..deps import get_context, require_user

router = APIRouter(
    prefix="/api/config", tags=["config"], dependencies=[Depends(require_user)]
)


def _mask_api_id(value) -> str:
    if not value:
        return ""
    s = str(value)
    if len(s) <= 4:
        return "*" * len(s)
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


class UiPreferencesBody(BaseModel):
    theme: str


@router.get("/ui")
async def get_ui_preferences(request: Request) -> dict:
    ctx = get_context(request)
    theme = (ctx.settings.get("ui_theme") or "dark").lower()
    if theme not in ("dark", "light"):
        theme = "dark"
    return {"theme": theme}


@router.put("/ui")
async def put_ui_preferences(request: Request, body: UiPreferencesBody) -> dict:
    theme = body.theme.lower().strip()
    if theme not in ("dark", "light"):
        raise HTTPException(status_code=400, detail="Theme phải là dark hoặc light.")
    ctx = get_context(request)
    ctx.settings.set("ui_theme", theme)
    return {"theme": theme}


@router.get("")
async def get_config(request: Request) -> dict:
    ctx = get_context(request)
    c = ctx.config
    return {
        "api_id_masked": _mask_api_id(c.api_id),
        "api_hash_set": bool(c.api_hash),
        "api_phone_number": (
            mask_phone(c.api_phone_number) if c.api_phone_number else None
        ),
        "proxy_set": bool(c.proxy),
        "database_path": str(c.database_path),
        "database_backend": "PostgreSQL" if getattr(ctx.db, "_use_postgres", False) else "SQLite",
        "default_phone_region": c.default_phone_region,
        "max_attempts": c.max_attempts,
        "base_retry_delay_seconds": c.base_retry_delay_seconds,
        "max_retry_delay_seconds": c.max_retry_delay_seconds,
        "min_request_interval_seconds": c.min_request_interval_seconds,
        "auto_resume": c.auto_resume,
        "worker_lease_seconds": c.worker_lease_seconds,
        "lease_takeover_grace_seconds": c.lease_takeover_grace_seconds,
        "in_flight_recovery_grace_seconds": c.in_flight_recovery_grace_seconds,
        "web_ui": {
            "username": ctx.auth.username,
            "session_hours": round(TOKEN_MAX_AGE_SECONDS / 3600, 2),
            "telegram_account_owned": await _account_owner(ctx),
        },
    }


async def _account_owner(ctx) -> bool:
    try:
        status = await ctx.account.status()
        return status.get("state") in ("AUTHORIZED", "IN_USE")
    except Exception:  # noqa: BLE001
        return False
