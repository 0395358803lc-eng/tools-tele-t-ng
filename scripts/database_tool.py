#!/usr/bin/env python3
"""Backup, verify and restore the application's database without logging secrets."""

import argparse
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import load_dotenv

load_dotenv()

DEFAULT_PG_CONTAINER = os.getenv("POSTGRES_CONTAINER", "check-telegram-postgres")


def _postgres_env(url: str) -> dict[str, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("Expected a PostgreSQL URL.")
    env = os.environ.copy()
    env.update({
        "PGHOST": parsed.hostname or "",
        "PGPORT": str(parsed.port or 5432),
        "PGDATABASE": unquote((parsed.path or "/").lstrip("/")),
        "PGUSER": unquote(parsed.username or ""),
        "PGPASSWORD": unquote(parsed.password or ""),
    })
    return env

def _postgres_parts(url: str) -> tuple[str, str, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("Expected a PostgreSQL URL.")
    user = unquote(parsed.username or "")
    database = unquote((parsed.path or "/").lstrip("/"))
    host = (parsed.hostname or "").lower()
    return user, database, host


def _docker_pg_available(url: str, container: str = DEFAULT_PG_CONTAINER) -> bool:
    if shutil.which("docker") is None:
        return False
    _user, _database, host = _postgres_parts(url)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return False
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def verify_sqlite(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"Backup not found: {path}")
    conn = sqlite3.connect(str(path))
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {result}")
    finally:
        conn.close()


def backup_sqlite(source: Path, output: Path) -> None:
    source = source.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(str(output))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    verify_sqlite(output)


def restore_sqlite(backup: Path, destination: Path, force: bool) -> None:
    verify_sqlite(backup)
    if destination.exists() and not force:
        raise RuntimeError("Destination exists; pass --force to replace it.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".restore-tmp")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(str(backup))
    dst = sqlite3.connect(str(tmp))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    verify_sqlite(tmp)
    os.replace(tmp, destination)


def backup_postgres(url: str, output: Path, container: str = DEFAULT_PG_CONTAINER) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("pg_dump") is not None:
        subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", str(output)],
            env=_postgres_env(url), check=True,
        )
        return
    if not _docker_pg_available(url, container):
        raise RuntimeError("pg_dump is unavailable and no local PostgreSQL container is running.")
    user, database, _host = _postgres_parts(url)
    with output.open("wb") as stream:
        subprocess.run(
            ["docker", "exec", container, "pg_dump", "-U", user, "-d", database,
             "--format=custom", "--no-owner", "--no-privileges"],
            stdout=stream, check=True,
        )

def verify_postgres_backup(path: Path, url: str, container: str = DEFAULT_PG_CONTAINER) -> None:
    if not path.is_file():
        raise RuntimeError(f"Backup not found: {path}")
    if shutil.which("pg_restore") is not None:
        subprocess.run(["pg_restore", "--list", str(path)], check=True, stdout=subprocess.DEVNULL)
        return
    if not _docker_pg_available(url, container):
        raise RuntimeError("pg_restore is unavailable and no local PostgreSQL container is running.")
    with path.open("rb") as stream:
        subprocess.run(
            ["docker", "exec", "-i", container, "pg_restore", "--list"],
            stdin=stream, stdout=subprocess.DEVNULL, check=True,
        )


def restore_postgres(url: str, backup: Path, force: bool, container: str = DEFAULT_PG_CONTAINER) -> None:
    if not force:
        raise RuntimeError("PostgreSQL restore requires --force.")
    verify_postgres_backup(backup, url, container)
    if shutil.which("pg_restore") is not None:
        subprocess.run(
            ["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges", str(backup)],
            env=_postgres_env(url), check=True,
        )
        return
    if not _docker_pg_available(url, container):
        raise RuntimeError("pg_restore is unavailable and no local PostgreSQL container is running.")
    user, database, _host = _postgres_parts(url)
    with backup.open("rb") as stream:
        subprocess.run(
            ["docker", "exec", "-i", container, "pg_restore", "-U", user, "-d", database,
             "--clean", "--if-exists", "--no-owner", "--no-privileges"],
            stdin=stream, check=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore", "verify"])
    parser.add_argument("--sqlite", type=Path, help="SQLite database path")
    parser.add_argument("--postgres-url", default=os.getenv("DATABASE_URL"), help=argparse.SUPPRESS)
    parser.add_argument("--container", default=DEFAULT_PG_CONTAINER, help="Local PostgreSQL container fallback")
    parser.add_argument("--file", type=Path, required=True, help="Backup file path")
    parser.add_argument("--force", action="store_true", help="Required for destructive restore")
    args = parser.parse_args()

    if bool(args.sqlite) == bool(args.postgres_url):
        raise SystemExit("Choose exactly one backend: --sqlite or DATABASE_URL/--postgres-url.")
    if args.sqlite:
        if args.action == "backup":
            backup_sqlite(args.sqlite, args.file)
        elif args.action == "restore":
            restore_sqlite(args.file, args.sqlite, args.force)
        else:
            verify_sqlite(args.file)
    else:
        if args.action == "backup":
            backup_postgres(args.postgres_url, args.file, args.container)
            verify_postgres_backup(args.file, args.postgres_url, args.container)
        elif args.action == "restore":
            restore_postgres(args.postgres_url, args.file, args.force, args.container)
        else:
            verify_postgres_backup(args.file, args.postgres_url, args.container)
    print(f"{args.action.upper()} OK: {args.file}")


if __name__ == "__main__":
    main()
