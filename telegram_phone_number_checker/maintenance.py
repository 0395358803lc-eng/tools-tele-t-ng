from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .database import Database
from .models import now_iso


def _cutoff(days: int) -> str:
    value = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def cleanup_retention(
    db: Database,
    audit_days: int = 90,
    security_message_days: int = 30,
) -> dict[str, int]:
    """Delete expired transient state without touching Telegram sessions/jobs."""
    now = now_iso()
    audit_cutoff = _cutoff(audit_days)
    security_cutoff = _cutoff(security_message_days)
    counts: dict[str, int] = {}
    with db.transaction():
        cur = db.execute("DELETE FROM web_sessions WHERE expires_at < ? OR (revoked_at IS NOT NULL AND revoked_at < ?)", (now, audit_cutoff))
        counts["web_sessions"] = int(cur.rowcount or 0)
        cur = db.execute("DELETE FROM telegram_login_sessions WHERE expires_at < ?", (now,))
        counts["telegram_login_sessions"] = int(cur.rowcount or 0)
        cur = db.execute("DELETE FROM manager_audit_logs WHERE created_at < ?", (audit_cutoff,))
        counts["manager_audit_logs"] = int(cur.rowcount or 0)
        cur = db.execute(
            "DELETE FROM manager_security_messages WHERE received_at IS NOT NULL AND received_at < ?",
            (security_cutoff,),
        )
        counts["manager_security_messages"] = int(cur.rowcount or 0)
    return counts
