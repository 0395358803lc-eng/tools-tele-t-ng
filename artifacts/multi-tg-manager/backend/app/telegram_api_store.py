"""Encrypted persistent storage for the active Telegram API credential pair."""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import AppSetting

SETTING_KEY = "telegram_api_credentials_v1"


def _fernet() -> Fernet:
    seed = (settings.TWOFA_ENCRYPTION_KEY or settings.SESSION_SECRET or "").strip()
    if not seed:
        raise RuntimeError("No application encryption key is configured")
    digest = hashlib.sha256(("mtm-telegram-api-v1:" + seed).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(api_id: int, api_hash: str) -> str:
    payload = json.dumps(
        {"api_id": int(api_id), "api_hash": api_hash},
        separators=(",", ":"),
    ).encode()
    return _fernet().encrypt(payload).decode()


def _decrypt(value: str) -> tuple[int, str]:
    try:
        data = json.loads(_fernet().decrypt(value.encode()).decode())
        return int(data["api_id"]), str(data["api_hash"])
    except (InvalidToken, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Stored Telegram API credentials could not be decrypted") from exc


async def load(db: AsyncSession, *, persist_env_bootstrap: bool = True) -> bool:
    row = await db.get(AppSetting, SETTING_KEY)
    if row:
        api_id, api_hash = _decrypt(row.value)
        settings.TG_API_ID = api_id
        settings.TG_API_HASH = api_hash
        return bool(api_id and api_hash)

    if settings.TG_API_ID and settings.TG_API_HASH:
        if persist_env_bootstrap:
            db.add(AppSetting(key=SETTING_KEY, value=_encrypt(settings.TG_API_ID, settings.TG_API_HASH)))
            await db.commit()
        return True
    return False


async def status(db: AsyncSession) -> dict:
    configured = bool(settings.TG_API_ID and settings.TG_API_HASH)
    return {
        "configured": configured,
        "api_id": int(settings.TG_API_ID) if configured else None,
        "api_hash_set": bool(settings.TG_API_HASH),
    }


async def save(db: AsyncSession, api_id: int, api_hash: str) -> None:
    encrypted = _encrypt(api_id, api_hash)
    row = await db.get(AppSetting, SETTING_KEY)
    if row:
        row.value = encrypted
    else:
        db.add(AppSetting(key=SETTING_KEY, value=encrypted))
    await db.commit()
    settings.TG_API_ID = int(api_id)
    settings.TG_API_HASH = api_hash


async def current_hash(db: AsyncSession) -> str | None:
    row = await db.get(AppSetting, SETTING_KEY)
    if row:
        _, api_hash = _decrypt(row.value)
        return api_hash
    return settings.TG_API_HASH or None
