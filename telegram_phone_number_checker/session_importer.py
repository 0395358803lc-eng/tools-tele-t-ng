import sqlite3
from pathlib import Path
from typing import Optional

from telethon import TelegramClient
from telethon.sessions import SQLiteSession, StringSession


class SessionImportError(ValueError):
    pass


def validate_telethon_sqlite(path: Path) -> None:
    with path.open("rb") as fh:
        if fh.read(16) != b"SQLite format 3\x00":
            raise SessionImportError("File không phải Telethon SQLite .session hợp lệ.")
    try:
        uri = f"file:{path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if ok != "ok" or "sessions" not in tables:
            raise SessionImportError("Cấu trúc file .session không hợp lệ hoặc bị hỏng.")
    except sqlite3.Error as exc:
        raise SessionImportError("Không thể đọc file SQLite session.") from exc


async def inspect_and_convert_session(
    path: Path,
    api_id: str,
    api_hash: str,
    proxy=None,
) -> dict:
    validate_telethon_sqlite(path)
    sqlite_session = SQLiteSession(str(path))
    try:
        exported = StringSession.save(sqlite_session)
    finally:
        sqlite_session.close()
    if not exported:
        raise SessionImportError("File session không chứa authorization key Telegram.")

    client = TelegramClient(StringSession(exported), int(api_id), api_hash, proxy=proxy)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise SessionImportError("Session Telegram đã hết hiệu lực hoặc chưa được đăng nhập.")
        me = await client.get_me()
        refreshed = StringSession.save(client.session)
        return {
            "session_string": refreshed,
            "phone": getattr(me, "phone", None),
            "telegram_user_id": getattr(me, "id", None),
            "username": getattr(me, "username", None),
            "first_name": getattr(me, "first_name", None),
        }
    finally:
        await client.disconnect()
