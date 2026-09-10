"""Encrypted PostgreSQL store for remembered Telegram 2FA passwords."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from .config import settings
from .db import AsyncSessionLocal
from .models import RememberedTwoFa

_lock = asyncio.Lock()
_migrated_legacy = False


def _norm_phone(phone: str) -> str:
    p = (phone or "").strip()
    digits = re.sub(r"[^0-9]", "", p)
    return f"+{digits}" if digits else p


def _fernet() -> Fernet:
    raw = (settings.TWOFA_ENCRYPTION_KEY or "").strip()
    if raw:
        try:
            return Fernet(raw.encode())
        except Exception:
            seed = raw
    else:
        seed = (settings.SESSION_SECRET or "").strip()
    if not seed:
        raise RuntimeError("No encryption key available for remembered Telegram 2FA passwords")
    digest = hashlib.sha256(("mtm-twofa-v1:" + seed).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _legacy_enc_path() -> Path:
    return settings.sessions_path / "twofa.enc"


def _legacy_json_path() -> Path:
    return settings.sessions_path / "twofa.json"


def _read_legacy_files() -> dict[str, str]:
    enc = _legacy_enc_path()
    plain = _legacy_json_path()
    if enc.exists():
        try:
            data = json.loads(_fernet().decrypt(enc.read_bytes()).decode("utf-8") or "{}")
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Legacy encrypted 2FA store could not be decrypted") from exc
    if plain.exists():
        try:
            data = json.loads(plain.read_text(encoding="utf-8") or "{}")
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            return {}
    return {}


async def _migrate_legacy_locked() -> None:
    global _migrated_legacy
    if _migrated_legacy:
        return
    data = _read_legacy_files()
    if data:
        async with AsyncSessionLocal() as db:
            for phone, password in data.items():
                key = _norm_phone(phone)
                if not key or not password:
                    continue
                row = await db.get(RememberedTwoFa, key)
                if row is None:
                    db.add(RememberedTwoFa(
                        phone=key,
                        ciphertext=_fernet().encrypt(password.encode("utf-8")),
                        updated_at=datetime.utcnow(),
                    ))
            await db.commit()
        for path in (_legacy_enc_path(), _legacy_json_path()):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    _migrated_legacy = True


async def save_2fa(phone: str, password: str) -> None:
    if not phone or not password:
        return
    key = _norm_phone(phone)
    async with _lock:
        await _migrate_legacy_locked()
        encrypted = _fernet().encrypt(password.encode("utf-8"))
        async with AsyncSessionLocal() as db:
            row = await db.get(RememberedTwoFa, key)
            if row:
                row.ciphertext = encrypted
                row.updated_at = datetime.utcnow()
            else:
                db.add(RememberedTwoFa(
                    phone=key,
                    ciphertext=encrypted,
                    updated_at=datetime.utcnow(),
                ))
            await db.commit()


async def get_2fa(phone: str) -> str | None:
    key = _norm_phone(phone)
    async with _lock:
        await _migrate_legacy_locked()
        async with AsyncSessionLocal() as db:
            row = await db.get(RememberedTwoFa, key)
        if not row:
            return None
        try:
            return _fernet().decrypt(bytes(row.ciphertext)).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("Stored 2FA password could not be decrypted") from exc


async def known_passwords() -> list[str]:
    async with _lock:
        await _migrate_legacy_locked()
        async with AsyncSessionLocal() as db:
            rows = list((await db.execute(select(RememberedTwoFa))).scalars().all())
        seen: list[str] = []
        for row in rows:
            try:
                value = _fernet().decrypt(bytes(row.ciphertext)).decode("utf-8")
            except InvalidToken as exc:
                raise RuntimeError("Stored 2FA password could not be decrypted") from exc
            if value and value not in seen:
                seen.append(value)
        return seen


async def count() -> int:
    async with _lock:
        await _migrate_legacy_locked()
        async with AsyncSessionLocal() as db:
            rows = list((await db.execute(select(RememberedTwoFa.phone))).all())
        return len(rows)


async def delete_2fa(phone: str) -> None:
    key = _norm_phone(phone)
    async with _lock:
        await _migrate_legacy_locked()
        async with AsyncSessionLocal() as db:
            row = await db.get(RememberedTwoFa, key)
            if row:
                await db.delete(row)
                await db.commit()


async def migrate_legacy_store() -> None:
    """Import legacy local 2FA files into PostgreSQL once, if they exist."""
    async with _lock:
        await _migrate_legacy_locked()
