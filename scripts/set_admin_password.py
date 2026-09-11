#!/usr/bin/env python3
"""Set the Web UI admin password as an scrypt hash without storing plaintext."""

from getpass import getpass
from pathlib import Path
import os

from telegram_phone_number_checker.webapi.auth import make_scrypt_hash


def main() -> None:
    first = getpass("New admin password: ")
    second = getpass("Confirm admin password: ")
    if first != second:
        raise SystemExit("Passwords do not match.")
    if len(first) < 12:
        raise SystemExit("Password must contain at least 12 characters.")

    path = Path(".env")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    blocked = {"WEB_UI_PASSWORD", "WEB_UI_PASSWORD_HASH", "WEB_UI_PASSWORD_SCRYPT"}
    kept = [line for line in lines if not (line.strip() and line.split("=", 1)[0].strip() in blocked)]
    kept.append(f"WEB_UI_PASSWORD_SCRYPT={make_scrypt_hash(first)}")
    if not any(line.startswith("WEB_UI_USERNAME=") for line in kept):
        kept.append("WEB_UI_USERNAME=admin")
    path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    print("Admin password hash updated successfully.")


if __name__ == "__main__":
    main()
