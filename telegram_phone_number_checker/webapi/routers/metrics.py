"""Authenticated operational metrics without secrets or raw phone numbers."""

from fastapi import APIRouter, Depends, Request

from ...models import now_iso
from ..deps import get_context, require_user

router = APIRouter(
    prefix="/api/metrics", tags=["metrics"], dependencies=[Depends(require_user)]
)


def _count_by_status(ctx, table: str) -> dict[str, int]:
    rows = ctx.db.execute(
        f"SELECT status, COUNT(*) AS c FROM {table} GROUP BY status"
    ).fetchall()
    return {str(row["status"]): int(row["c"] or 0) for row in rows}


@router.get("")
async def metrics(request: Request) -> dict:
    ctx = get_context(request)
    now = now_iso()
    jobs_by_status = _count_by_status(ctx, "jobs")
    items_by_status = _count_by_status(ctx, "check_items")
    scalar_queries = {
        "telegram_accounts": "SELECT COUNT(*) AS c FROM telegram_accounts",
        "active_job_leases": "SELECT COUNT(*) AS c FROM jobs WHERE worker_lease_until IS NOT NULL AND worker_lease_until > ?",
        "active_account_leases": "SELECT COUNT(*) AS c FROM account_worker_state WHERE worker_lease_until IS NOT NULL AND worker_lease_until > ?",
        "flood_wait_accounts": "SELECT COUNT(*) AS c FROM account_runtime_state WHERE blocked_until IS NOT NULL AND blocked_until > ?",
        "audit_rows": "SELECT COUNT(*) AS c FROM manager_audit_logs",
    }
    values = {}
    for key, sql in scalar_queries.items():
        params = (now,) if "?" in sql else ()
        row = ctx.db.execute(sql, params).fetchone()
        values[key] = int(row["c"] or 0) if row else 0
    return {
        "timestamp": now,
        "database_backend": "PostgreSQL" if getattr(ctx.db, "_use_postgres", False) else "SQLite",
        "jobs_total": sum(jobs_by_status.values()),
        "jobs_by_status": jobs_by_status,
        "items_total": sum(items_by_status.values()),
        "items_by_status": items_by_status,
        **values,
    }
