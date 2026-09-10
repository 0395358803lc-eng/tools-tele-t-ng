"""Encrypted PostgreSQL-backed canonical store for Telethon SQLite sessions."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select

from .config import settings
from .db import AsyncSessionLocal
from .models import Account, TelegramSessionBlob


def _fernet() -> Fernet:
    seed = (settings.TWOFA_ENCRYPTION_KEY or settings.SESSION_SECRET or "").strip()
    if not seed:
        raise RuntimeError("No encryption key available for Telegram sessions")
    digest = hashlib.sha256(("mtm-telegram-session-v1:" + seed).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _snapshot_sqlite(path: str) -> bytes:
    src_path = Path(path)
    if not src_path.exists():
        raise FileNotFoundError(path)
    fd, tmp_name = tempfile.mkstemp(prefix="mtm_session_snapshot_", suffix=".sqlite")
    os.close(fd)
    try:
        source = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
        target = sqlite3.connect(tmp_name)
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()
        data = Path(tmp_name).read_bytes()
        if not data.startswith(b"SQLite format 3\x00"):
            raise RuntimeError("Invalid Telethon SQLite session snapshot")
        return data
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def _restore_bytes(path: str, data: bytes) -> None:
    if not data.startswith(b"SQLite format 3\x00"):
        raise RuntimeError("Stored Telegram session is not a SQLite database")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".restore")
    tmp.write_bytes(data)
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    os.replace(tmp, target)
    for suffix in ["-journal", "-wal", "-shm"]:
        try:
            Path(str(target) + suffix).unlink()
        except FileNotFoundError:
            pass


async def save_file(account_id: int, session_path: str) -> bool:
    try:
        data = await asyncio.to_thread(_snapshot_sqlite, session_path)
    except FileNotFoundError:
        return False
    digest = hashlib.sha256(data).hexdigest()
    encrypted = _fernet().encrypt(data)
    async with AsyncSessionLocal() as db:
        row = await db.get(TelegramSessionBlob, account_id)
        if row and row.sha256 == digest:
            return True
        if row:
            row.ciphertext = encrypted
            row.sha256 = digest
            row.updated_at = datetime.utcnow()
        else:
            db.add(TelegramSessionBlob(
                account_id=account_id,
                ciphertext=encrypted,
                sha256=digest,
                updated_at=datetime.utcnow(),
            ))
        await db.commit()
    return True


async def restore_file(account_id: int, session_path: str) -> bool:
    async with AsyncSessionLocal() as db:
        row = await db.get(TelegramSessionBlob, account_id)
        if not row:
            return False
        ciphertext = bytes(row.ciphertext)
        expected = row.sha256
    try:
        data = _fernet().decrypt(ciphertext)
    except InvalidToken as exc:
        raise RuntimeError("Stored Telegram session could not be decrypted") from exc
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise RuntimeError("Stored Telegram session checksum mismatch")
    await asyncio.to_thread(_restore_bytes, session_path, data)
    return True


async def restore_or_capture(account_id: int, session_path: str) -> str:
    """Use PostgreSQL as canonical; import a local session only when DB has none."""
    async with AsyncSessionLocal() as db:
        row = await db.get(TelegramSessionBlob, account_id)
    local = Path(session_path)
    if row:
        await restore_file(account_id, session_path)
        return "restored"
    if local.exists() and await save_file(account_id, session_path):
        return "captured"
    return "missing"


async def all_accounts_backed_up() -> bool:
    """True when every active Account has a canonical encrypted DB session."""
    async with AsyncSessionLocal() as db:
        missing = await db.scalar(
            select(func.count(Account.id))
            .outerjoin(TelegramSessionBlob, TelegramSessionBlob.account_id == Account.id)
            .where(TelegramSessionBlob.account_id.is_(None))
        )
    return int(missing or 0) == 0
