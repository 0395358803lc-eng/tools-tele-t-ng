import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import telegram_api_store
from ..config import settings as env_settings
from ..db import get_db
from ..models import Account, AppSetting, RememberedTwoFa, TelegramSessionBlob
from ..schemas import (
    SettingsIn,
    SettingsOut,
    TelegramApiCredentialsIn,
    TelegramApiCredentialsOut,
    ThemePreferenceIn,
    ThemePreferenceOut,
)
from ..tg_manager import manager

router = APIRouter(prefix="/api/settings", tags=["settings"])

DEFAULTS = {
    "rate_min": "0.7",
    "rate_max": "1.5",
    "recipient_send_delay_min": str(env_settings.RECIPIENT_SEND_DELAY_MIN),
    "recipient_send_delay_max": str(env_settings.RECIPIENT_SEND_DELAY_MAX),
    "concurrency": "8",
    "sessions_dir": env_settings.SESSIONS_DIR,
    "auto_reconnect": "true",
    "notification_sound": "true",
    "theme": "dark",
}


async def load_runtime_settings():
    """Apply persisted runtime settings before Telegram clients start."""
    from ..db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        cur = await _read_all(db)
        await telegram_api_store.load(db)
    try:
        env_settings.RATE_MIN = float(cur.get("rate_min", env_settings.RATE_MIN))
        env_settings.RATE_MAX = float(cur.get("rate_max", env_settings.RATE_MAX))
        env_settings.RECIPIENT_SEND_DELAY_MIN = float(cur.get("recipient_send_delay_min", env_settings.RECIPIENT_SEND_DELAY_MIN))
        env_settings.RECIPIENT_SEND_DELAY_MAX = float(cur.get("recipient_send_delay_max", env_settings.RECIPIENT_SEND_DELAY_MAX))
        env_settings.CONCURRENCY = min(
            50, max(1, int(float(cur.get("concurrency", env_settings.CONCURRENCY))))
        )
    except (TypeError, ValueError):
        pass
    sessions_dir = (cur.get("sessions_dir") or "").strip()
    if sessions_dir:
        env_settings.SESSIONS_DIR = sessions_dir
    manager.auto_reconnect = cur.get("auto_reconnect", "true") == "true"


async def _read_all(db: AsyncSession) -> dict[str, str]:
    res = await db.execute(select(AppSetting))
    rows = res.scalars().all()
    cur = {r.key: r.value for r in rows}
    missing = {k: v for k, v in DEFAULTS.items() if k not in cur}
    if missing:
        for k, v in missing.items():
            db.add(AppSetting(key=k, value=v))
            cur[k] = v
        await db.commit()
    return cur


@router.get("", response_model=SettingsOut)
async def get_settings(db: AsyncSession = Depends(get_db)):
    cur = await _read_all(db)
    try:
        conc = int(float(cur.get("concurrency", "5")))
    except (TypeError, ValueError):
        conc = 5
    return SettingsOut(
        rate_min=float(cur["rate_min"]),
        rate_max=float(cur["rate_max"]),
        recipient_send_delay_min=float(cur.get("recipient_send_delay_min", "3")),
        recipient_send_delay_max=float(cur.get("recipient_send_delay_max", "7")),
        concurrency=max(1, conc),
        sessions_dir=cur["sessions_dir"],
        auto_reconnect=cur["auto_reconnect"] == "true",
        notification_sound=cur["notification_sound"] == "true",
        theme=cur.get("theme", "dark"),
    )


@router.put("", response_model=SettingsOut)
async def update_settings(body: SettingsIn, db: AsyncSession = Depends(get_db)):
    conc = min(50, max(1, int(body.concurrency or 5)))
    payload = {
        "rate_min": str(body.rate_min),
        "rate_max": str(body.rate_max),
        "recipient_send_delay_min": str(body.recipient_send_delay_min),
        "recipient_send_delay_max": str(body.recipient_send_delay_max),
        "concurrency": str(conc),
        "sessions_dir": body.sessions_dir,
        "auto_reconnect": "true" if body.auto_reconnect else "false",
        "notification_sound": "true" if body.notification_sound else "false",
        "theme": body.theme,
    }
    res = await db.execute(select(AppSetting))
    existing = {r.key: r for r in res.scalars().all()}
    for k, v in payload.items():
        if k in existing:
            existing[k].value = v
        else:
            db.add(AppSetting(key=k, value=v))
    await db.commit()
    # apply rate + concurrency to env_settings live (no restart needed)
    env_settings.RATE_MIN = body.rate_min
    env_settings.RATE_MAX = body.rate_max
    env_settings.RECIPIENT_SEND_DELAY_MIN = body.recipient_send_delay_min
    env_settings.RECIPIENT_SEND_DELAY_MAX = body.recipient_send_delay_max
    env_settings.CONCURRENCY = conc
    manager.auto_reconnect = body.auto_reconnect
    return await get_settings(db)


@router.get("/storage_status")
async def storage_status(db: AsyncSession = Depends(get_db)):
    accounts = int(await db.scalar(select(func.count(Account.id))) or 0)
    session_blobs = int(await db.scalar(select(func.count(TelegramSessionBlob.account_id))) or 0)
    missing = int(
        await db.scalar(
            select(func.count(Account.id))
            .outerjoin(TelegramSessionBlob, TelegramSessionBlob.account_id == Account.id)
            .where(TelegramSessionBlob.account_id.is_(None))
        )
        or 0
    )
    remembered_2fa = int(await db.scalar(select(func.count(RememberedTwoFa.phone))) or 0)
    return {
        "database": "postgresql",
        "accounts": accounts,
        "session_blobs": session_blobs,
        "missing_session_blobs": missing,
        "remembered_2fa": remembered_2fa,
        "browser_persistence": False,
        "portable_ready": missing == 0,
    }


@router.get("/export")
async def export_json(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Account))
    accounts = res.scalars().all()
    out = []
    for a in accounts:
        out.append(
            {
                "id": a.id,
                "phone": a.phone,
                "first_name": a.first_name,
                "last_name": a.last_name,
                "username": a.username,
                "bio": a.bio,
                "status": a.status,
                "has_2fa": a.has_2fa,
                "tg_user_id": a.tg_user_id,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
        )
    return {
        "exported_at": datetime.utcnow().isoformat(),
        "count": len(out),
        "accounts": out,
    }

@router.put("/theme", response_model=ThemePreferenceOut)
async def update_theme(body: ThemePreferenceIn, db: AsyncSession = Depends(get_db)):
    row = await db.get(AppSetting, "theme")
    if row:
        row.value = body.theme
    else:
        db.add(AppSetting(key="theme", value=body.theme))
    await db.commit()
    return ThemePreferenceOut(theme=body.theme)


@router.get("/telegram_api", response_model=TelegramApiCredentialsOut)
async def get_telegram_api(db: AsyncSession = Depends(get_db)):
    return TelegramApiCredentialsOut(**(await telegram_api_store.status(db)))


@router.put("/telegram_api", response_model=TelegramApiCredentialsOut)
async def update_telegram_api(
    body: TelegramApiCredentialsIn, db: AsyncSession = Depends(get_db)
):
    api_hash = (body.api_hash or "").strip()
    current = await telegram_api_store.status(db)
    previous_hash = await telegram_api_store.current_hash(db)

    if not api_hash:
        if not current["configured"]:
            raise HTTPException(400, "TG_API_HASH is required for the first configuration")
        if int(current["api_id"] or 0) != int(body.api_id):
            raise HTTPException(
                400, "TG_API_HASH is required when TG_API_ID changes"
            )
        api_hash = previous_hash or ""

    if not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        raise HTTPException(400, "TG_API_HASH must be exactly 32 hexadecimal characters")

    changed = (
        not current["configured"]
        or int(current["api_id"] or 0) != int(body.api_id)
        or previous_hash != api_hash
    )
    await telegram_api_store.save(db, body.api_id, api_hash)

    should_reconnect = body.reconnect_existing or not current["configured"]
    reload_result = {"reconnected": 0, "failed": 0, "results": []}
    if should_reconnect:
        reload_result = await manager.rebuild_all_clients_for_api_credentials()

    state = await telegram_api_store.status(db)
    return TelegramApiCredentialsOut(
        **state,
        **reload_result,
        reconnect_required=bool(changed and not should_reconnect),
    )

