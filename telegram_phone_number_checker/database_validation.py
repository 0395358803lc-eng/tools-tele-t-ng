"""Database invariant checks for telegram-phone-number-checker.

Uses a single connection (or a Database) to detect states that indicate a
broken job lifecycle / orphaned worker-account lease. Returns a list of
(severity, message) findings; empty list = consistent.

Exposed through the CLI: `telegram-phone-number-checker job validate`.
"""

from typing import List, Tuple

from .database import Database
from .models import now_iso, parse_iso

TERMINAL_ITEM_STATUSES = ("FOUND", "NOT_DISCOVERABLE", "PERMANENT_ERROR")
NON_TERMINAL_ITEM_STATUSES = (
    "PENDING",
    "PROCESSING",
    "IN_FLIGHT_UNKNOWN",
    "RETRY_REQUIRED",
    "TEMPORARY_ERROR",
    "RATE_LIMITED",
)

Finding = Tuple[str, str]  # (severity, message)


def _col(row, name, default=None):
    try:
        return row[name]
    except (KeyError, IndexError):
        return default


def _epoch_after(iso_value, now_epoch) -> bool:
    if not iso_value:
        return False
    dt = parse_iso(iso_value)
    if dt is None:
        return False
    return dt.timestamp() > now_epoch


def validate_database_state(db: Database) -> List[Finding]:
    """Scan the whole database for lifecycle / ownership inconsistencies."""
    findings: List[Finding] = []
    now = now_iso()
    now_epoch = parse_iso(now).timestamp() if parse_iso(now) else 0.0

    # --- Job-level invariants ------------------------------------------------
    jobs = db.execute("SELECT * FROM jobs").fetchall()
    for row in jobs:
        jid = row["id"]
        status = row["status"]
        worker_id = _col(row, "worker_id")
        worker_lease = _col(row, "worker_lease_until")
        heartbeat = _col(row, "worker_heartbeat_at")

        if status == "COMPLETED" and worker_id is not None:
            findings.append(("ERROR", f"job {jid} COMPLETED but worker_id is not NULL"))
        if status == "COMPLETED" and heartbeat is not None:
            findings.append(
                ("ERROR", f"job {jid} COMPLETED but worker_heartbeat_at is set")
            )
        if status == "PAUSED" and _epoch_after(worker_lease, now_epoch):
            findings.append(
                ("ERROR", f"job {jid} PAUSED but worker lease still active")
            )
        if status == "COMPLETED" and _epoch_after(worker_lease, now_epoch):
            findings.append(
                ("ERROR", f"job {jid} COMPLETED but worker lease still active")
            )
        # Phase 13: RUNNING but no live worker ownership -> the job is stuck
        # RUNNING with nobody actively working it (WARNING; recovery may claim).
        if status == "RUNNING" and worker_id is None:
            findings.append(("WARN", f"job {jid} RUNNING but has no worker ownership"))

    # --- Job PAUSED must not hold an active account lease (Phase 13) ---------
    for row in jobs:
        jid = row["id"]
        if row["status"] == "PAUSED":
            acct = db.execute(
                "SELECT worker_id, worker_lease_until FROM account_worker_state "
                "WHERE job_id = ?",
                (jid,),
            ).fetchone()
            if (
                acct
                and acct["worker_id"]
                and _epoch_after(acct["worker_lease_until"], now_epoch)
            ):
                findings.append(
                    (
                        "ERROR",
                        f"job {jid} PAUSED but account lease still active for "
                        f"worker {acct['worker_id']}",
                    )
                )

    # --- Item-level invariants -----------------------------------------------
    items = db.execute("SELECT * FROM check_items").fetchall()
    for row in items:
        iid = row["id"]
        status = row["status"]
        completed_at = _col(row, "completed_at")
        if status in TERMINAL_ITEM_STATUSES and completed_at is None:
            findings.append(
                ("ERROR", f"item {iid} is terminal {status} but completed_at is NULL")
            )
        if status in NON_TERMINAL_ITEM_STATUSES and completed_at is not None:
            findings.append(
                ("WARN", f"item {iid} non-terminal {status} but completed_at is set")
            )
        if status == "PROCESSING" and not _col(row, "processing_token"):
            findings.append(
                ("ERROR", f"item {iid} PROCESSING but processing_token is NULL")
            )
        if status == "IN_FLIGHT_UNKNOWN" and not _col(row, "recovery_after"):
            findings.append(
                ("ERROR", f"item {iid} IN_FLIGHT_UNKNOWN but recovery_after is NULL")
            )

    # --- Job COMPLETED must have NO unfinished items --------------------------
    for row in jobs:
        jid = row["id"]
        status = row["status"]
        c = db.execute(
            "SELECT COUNT(*) AS c FROM check_items WHERE job_id = ? "
            "AND status NOT IN ('FOUND','NOT_DISCOVERABLE','PERMANENT_ERROR')",
            (jid,),
        ).fetchone()
        unfinished = c["c"] if c else 0
        if status == "COMPLETED" and unfinished > 0:
            findings.append(
                (
                    "ERROR",
                    f"job {jid} COMPLETED but has {unfinished} unfinished item(s)",
                )
            )

    # --- Orphan worker / account leases --------------------------------------
    # Any job whose lease is active in the DB but which is terminal.
    for row in jobs:
        jid = row["id"]
        status = row["status"]
        if status in ("COMPLETED", "FAILED", "CANCELLED"):
            ok = db.execute(
                "SELECT worker_id IS NOT NULL OR worker_lease_until IS NOT NULL "
                "AS active FROM jobs WHERE id = ?",
                (jid,),
            ).fetchone()
            if ok and (ok["active"] or 0):
                findings.append(
                    ("ERROR", f"job {jid} {status} still holds worker ownership")
                )

    # Orphan account_worker_state entries (pointing at a job that is terminal).
    acct_rows = db.execute("SELECT * FROM account_worker_state").fetchall()
    for row in acct_rows:
        job_id = _col(row, "job_id")
        lease = _col(row, "worker_lease_until")
        if job_id is None:
            continue
        jrow = db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()

        # P1-04: the account-leased worker must MATCH the job's owned worker; a
        # mismatch means ownership got split between job and account lease.
        acct_worker = _col(row, "worker_id")
        if jrow is not None:
            j_worker = _col(
                db.execute(
                    "SELECT worker_id FROM jobs WHERE id = ?", (job_id,)
                ).fetchone(),
                "worker_id",
            )
            if (
                acct_worker
                and j_worker
                and acct_worker != j_worker
                and (_col(row, "worker_lease_until") is not None)
                and _epoch_after(_col(row, "worker_lease_until"), now_epoch)
            ):
                findings.append(
                    (
                        "ERROR",
                        f"account worker {acct_worker} != job worker {j_worker} "
                        f"for active lease on job {job_id}",
                    )
                )
        if (
            jrow
            and jrow["status"] in ("COMPLETED", "FAILED", "CANCELLED")
            and _epoch_after(lease, now_epoch)
        ):
            findings.append(
                (
                    "ERROR",
                    f"account_worker_state row has live lease for terminal job {job_id}",
                )
            )

    # --- Duplicate worker: one worker_id owning more than one active job ------
    dup = db.execute("""
        SELECT worker_id, COUNT(*) AS n FROM jobs
        WHERE worker_id IS NOT NULL AND status IN ('RUNNING','RATE_LIMITED')
        GROUP BY worker_id HAVING COUNT(*) > 1
        """).fetchall()
    for row in dup:
        findings.append(
            (
                "ERROR",
                f"worker {row['worker_id']} owns {row['n']} active jobs "
                "(duplicate worker)",
            )
        )

    return findings
