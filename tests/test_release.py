"""Phase 16 - Test H + Phase 13/14 assertions: clean release.

Builds the release ZIP through the real build script and asserts:
  - required files present: README, LICENSE, pyproject.toml, source, .env.example
  - forbidden files absent: .env, *.session, *.db, .git, __pycache__,
    .pytest_cache, and no plaintext blocklisted secret
  - a build fails (exit 1) if a blocklisted secret is present in an included file
"""

import hashlib
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_release.py"
BLOCKLIST = REPO_ROOT / "scripts" / "blocked_secret_hashes.txt"
# Synthetic secret used to prove the scanner catches leaked values. It is NOT a
# real credential: real values never appear in this repo, only their SHA-256
# hashes do (see scripts/blocked_secret_hashes.txt).
SYNTHETIC_SECRET = "synthetic_leak_probe_01"
SYNTHETIC_HASH = hashlib.sha256(SYNTHETIC_SECRET.encode()).hexdigest()


def _blocked_hashes() -> set:
    return {
        l.strip().lower()
        for l in BLOCKLIST.read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.startswith("#")
    }


def _build(out_zip: Path, blocklist: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if blocklist is not None:
        env["SECRET_BLOCKLIST"] = str(blocklist)
    return subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), "--out", str(out_zip)],
        capture_output=True,
        text=True,
        env=env,
    )


def test_clean_release_zip(tmp_path):
    out = tmp_path / "release.zip"
    proc = _build(out)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())

    # Required present
    for required in (
        "README.md", "LICENSE", "pyproject.toml", ".env.example",
        "scripts/database_tool.py", "scripts/provision_postgres.sh",
        "scripts/server_ctl.sh", "scripts/set_admin_password.py",
    ):
        assert required in names, f"missing {required}"
    # Source present
    assert "telegram_phone_number_checker/main.py" in names

    # Forbidden absent. Treat as path segments (dirs) or filename/exact checks.
    assert ".env" not in names  # the exact secrets file (…example is allowed)
    for n in names:
        parts = n.split("/")
        assert ".git" not in parts, f"git dir shipped: {n}"
        assert "__pycache__" not in parts, f"pycache shipped: {n}"
        assert ".pytest_cache" not in parts, f"pytest cache shipped: {n}"
        base = parts[-1].lower()
        assert not base.endswith(".session"), f"session shipped: {n}"
        assert not base.endswith(".db"), f"db shipped: {n}"
        assert not base.endswith((".db-wal", ".db-shm")), f"db shipped: {n}"

    # No blocklisted secret in any shipped text file
    blocked = _blocked_hashes()
    token_re = re.compile(r"[^\s,;='\":()\[\]{}]+")
    for n in names:
        if n.endswith((".py", ".md", ".toml", ".txt", ".example")):
            text = zipfile.ZipFile(out).read(n).decode("utf-8", "replace")
            for tok in token_re.findall(text):
                h = hashlib.sha256(tok.encode()).hexdigest()
                assert h.lower() not in blocked, f"secret leaked in {n}"


def test_build_fails_on_leaked_secret(tmp_path):
    # Use a synthetic secret + temp blocklist so no real credential is committed.
    blocklist = tmp_path / "blocked.txt"
    blocklist.write_text("# synthetic test\n" + SYNTHETIC_HASH + "\n", encoding="utf-8")

    probe = REPO_ROOT / "__leak_probe_e2e.tmp"
    probe.write_text(f"probe {SYNTHETIC_SECRET}\n", encoding="utf-8")
    try:
        proc = _build(tmp_path / "bad.zip", blocklist=blocklist)
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "FAIL" in proc.stdout
    finally:
        probe.unlink(missing_ok=True)


def test_blocklist_has_no_plaintext():
    lines = [
        ln.strip()
        for ln in BLOCKLIST.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    # Every non-comment entry must be a 64-char SHA-256 hex digest — i.e. a
    # hash, never a readable plaintext credential (an api_hash/phone are not
    # 64-char hex strings).
    assert lines, "blocklist is empty"
    for entry in lines:
        assert re.fullmatch(r"[0-9a-f]{64}", entry), f"non-hash entry: {entry!r}"
