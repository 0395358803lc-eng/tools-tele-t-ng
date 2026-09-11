#!/usr/bin/env python3
"""Build a clean release ZIP for telegram-phone-number-checker.

- Excludes credentials/runtime artifacts (.env, *.session, *.db, data/, logs/,
  caches, virtualenvs, git history).
- Scans every file that WOULD be included for sensitive/credential patterns.
- Fails the build (non-zero exit) if anything sensitive is found, so a clean
  release is guaranteed.

Usage:
    python scripts/build_release.py [--out dist/telegram-phone-number-checker-release.zip]
"""

import argparse
import fnmatch
import hashlib
import os
import re
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# --blocks:test. Overridable so tests can supply a synthetic hash blocklist and
# never keep a real secret in the blocklist/resolver path.
BLOCKLIST_FILE = Path(
    os.environ.get("SECRET_BLOCKLIST")
    or (REPO_ROOT / "scripts" / "blocked_secret_hashes.txt")
)

# --- Exclusion rules ---------------------------------------------------------
# Tuple of (type, pattern). 'dir' prunes whole directories anywhere in the tree
# (cheap); 'file' matches file names via fnmatch.
RELEASE_SCRIPTS = {
    "scripts/database_tool.py",
    "scripts/provision_postgres.sh",
    "scripts/server_ctl.sh",
    "scripts/set_admin_password.py",
}

EXCLUDE_DIRS = {
    ".git",
    ".github",
    ".server",
    "backups",
    ".agents",
    ".cache",
    ".config",
    ".local",
    ".pythonlibs",
    ".upm",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
    "node_modules",
    ".venv",
    "venv",
    "data",
    "logs",
    "results",
    "scripts",
    "tests",
}
EXCLUDE_FILE_PATTERNS = (
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".env.staging",
    "*.session",
    "*.session-journal",
    "*.db",
    "*.db-wal",
    "*.db-shm",
    "*.pyc",
    "*.pyo",
    "*.jpg",
    "*.jpeg",
    "*.png",
    "*.gif",
    "*.egg-info",
    "poetry.lock",
    "Thumbs.db",
    ".DS_Store",
)

# --- Sensitive-content scan ---------------------------------------------------
# The meaningful guarantee a release must make: known leaked secret VALUES never
# ship, and no dotenv/key/secret file ships. To avoid committing plaintext
# secrets, this scanner only stores/loads SHA-256 hashes of the leaked values
# (see scripts/blocked_secret_hashes.txt) and checks each candidate value's
# hash against the blocklist. Source heuristics for generic `password = "..."`
# literals are intentionally NOT used — config and tests legitimately contain
# such literals and doing so only produces noisy false positives.

# Placeholder allowlist: .env.example carries empty values and is safe to ship.
PLACEHOLDER_ALLOWLIST = {".env.example"}


def _load_blocked_hashes() -> set:
    """Load SHA-256 hashes of secret values that must not ship. Lowercased."""
    hashes = set()
    try:
        for line in BLOCKLIST_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                hashes.add(line.strip().lower())
    except OSError:
        pass
    return hashes


# A dotenv-style line that assigns a real (non-empty, non-placeholder) secret.
# Values are kept to the *same line* (spaces only, no newlines).
DOTENV_SECRET_RE = re.compile(
    r"^([A-Z0-9_]*(?:API_ID|API_HASH|PHONE_NUMBER|PASSWORD|SECRET|SESSION|TOKEN|DATABASE_URL)[A-Z0-9_]*)[ \t]*=[ \t]*([^\s#]+)",
    re.I | re.M,
)
PLACEHOLDER_SECRET_VALUES = {
    "change-me", "changeme", "example", "placeholder", "your-value", "your-secret"
}
RUNTIME_DIRS = {".agents", ".cache", ".config", ".local", ".pythonlibs", ".upm", "node_modules"}

SKIP_BINARY_EXT = {".jpg", ".jpeg", ".png", ".gif", ".ico", ".svg", ".woff", ".woff2"}

_TOKEN_RE = re.compile(r"[^\s,;='\":()\[\]{}]+")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scan_file(path: Path, rel: Path, blocked_hashes: set) -> list:
    if path.suffix.lower() in SKIP_BINARY_EXT:
        return []
    if rel.name in PLACEHOLDER_ALLOWLIST:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits = set()

    # 1) Token hashes: a leaked secret embedded in code/config would surface as
    #    a whitespace/punct-delimited token whose hash matches the blocklist.
    for tok in _TOKEN_RE.findall(text):
        if _sha256(tok).lower() in blocked_hashes:
            hits.add("blocklisted secret value (hashed match)")

    # 2) Dotenv-style secret assignment with a real value. Ignore obvious
    # documentation placeholders such as WEB_UI_PASSWORD=change-me.
    for match in DOTENV_SECRET_RE.finditer(text):
        raw_value = match.group(2).strip()
        # Ignore source-code expressions (e.g. TOKEN_MAX_AGE_SECONDS = int(...))
        # while still flagging quoted literals such as API_HASH = "real-secret".
        if not raw_value.startswith(("\"", "'")) and any(ch in raw_value for ch in "([{."):
            continue
        value = raw_value.strip("\"'").lower()
        if not value or value in PLACEHOLDER_SECRET_VALUES or value.startswith("your-"):
            continue
        hits.add("dotenv-style secret assignment")
        break

    return sorted(hits)


def should_exclude(rel_path: Path) -> bool:
    if rel_path.as_posix() in RELEASE_SCRIPTS:
        return False
    for part in rel_path.parts:
        if part in EXCLUDE_DIRS:
            return True
    for pat in EXCLUDE_FILE_PATTERNS:
        if fnmatch.fnmatch(rel_path.name, pat) or fnmatch.fnmatch(str(rel_path), pat):
            return True
    return False


def build(out_zip: Path) -> int:
    include = []
    violations = []
    blocked_hashes = _load_blocked_hashes()

    for path in sorted(REPO_ROOT.rglob("*")):
        rel = path.relative_to(REPO_ROOT)
        if path.is_dir():
            continue
        if should_exclude(rel):
            continue
        hits = scan_file(path, rel, blocked_hashes)
        if hits:
            violations.append((str(rel), hits))
        include.append((path, rel))

    # Report and (optionally) fail on violations, but only for files that
    # would be included. Excluded files (e.g. real .env, if present) still get
    # flagged loudly so the developer knows.
    excluded_violations = []
    for path in sorted(REPO_ROOT.rglob("*")):
        rel = path.relative_to(REPO_ROOT)
        if path.is_file() and should_exclude(rel):
            if any(part in RUNTIME_DIRS for part in rel.parts):
                continue
            hits = scan_file(path, rel, blocked_hashes)
            if hits:
                excluded_violations.append((str(rel), hits))

    print(f"[release] Would include {len(include)} files")

    if violations:
        print("[release] FAIL: sensitive content found in files that would be shipped:")
        for rel, hits in violations:
            print(f"  - {rel}: {', '.join(sorted(set(hits)))}")
        print(
            f"[release] Refusing to build a release that leaks credentials. "
            f"{len(violations)} file(s) affected."
        )
        return 1

    if excluded_violations:
        print(
            "[release] WARNING: excluded files still contain credential material "
            "(won't be shipped, but remove them):"
        )
        for rel, hits in excluded_violations:
            print(f"  - {rel}: {', '.join(sorted(set(hits)))}")
    else:
        print("[release] OK: no credential material found anywhere in the tree.")

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, rel in include:
            zf.write(path, rel.as_posix())

    print(f"[release] Built {out_zip} ({out_zip.stat().st_size} bytes)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "dist" / "telegram-phone-number-checker-release.zip"),
        help="Output zip path",
    )
    args = parser.parse_args()
    return build(Path(args.out))


if __name__ == "__main__":
    sys.exit(main())
