from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from sqlalchemy import inspect

from app.db import engine

ROOT = Path(__file__).resolve().parent


async def detect_state() -> str:
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    await engine.dispose()
    if "alembic_version" in tables:
        return "managed"
    if "accounts" in tables:
        return "legacy"
    return "fresh"


def alembic(*args: str) -> None:
    subprocess.check_call([sys.executable, "-m", "alembic", *args], cwd=ROOT)


def main() -> None:
    state = asyncio.run(detect_state())
    if state == "legacy":
        print("Existing legacy schema detected; stamping baseline.")
        alembic("stamp", "0001_baseline")
    alembic("upgrade", "head")


if __name__ == "__main__":
    main()
