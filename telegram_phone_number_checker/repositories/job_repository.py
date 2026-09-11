from typing import List, Optional

from ..database import Database
from ..models import Job, JobStatus, now_iso, parse_iso


def _to_epoch(value) -> Optional[float]:
    if not value:
        return None
    dt = parse_iso(value)
    return dt.timestamp() if dt is not None else None


class JobRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(
        self, job_id: str, name: Optional[str] = None, total_items: int = 0,
        telegram_account_phone: Optional[str] = None, job_mode: str = "SINGLE",
        parent_job_id: Optional[str] = None,
    ) -> Job:
        now = now_iso()
        self.db.execute(
            """
            INSERT INTO jobs (id, name, status, total_items, telegram_account_phone, job_mode, parent_job_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, name, JobStatus.CREATED.value, total_items, telegram_account_phone, job_mode, parent_job_id, now, now),
        )
        self.db.commit()
        return Job(
            id=job_id,
            name=name,
            status=JobStatus.CREATED,
            total_items=total_items,
            telegram_account_phone=telegram_account_phone,
            job_mode=job_mode,
            parent_job_id=parent_job_id,
            created_at=now,
            updated_at=now,
        )

    def get(self, job_id: str) -> Optional[Job]:
        row = self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def list(self) -> List[Job]:
        rows = self.db.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [self._row_to_job(r) for r in rows]

    def list_roots(self) -> List[Job]:
        rows = self.db.execute(
            "SELECT * FROM jobs WHERE parent_job_id IS NULL ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def list_children(self, parent_job_id: str) -> List[Job]:
        rows = self.db.execute(
            "SELECT * FROM jobs WHERE parent_job_id = ? ORDER BY created_at, id",
            (parent_job_id,),
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def is_multi_parent(self, job_id: str) -> bool:
        job = self.get(job_id)
        return bool(job and job.job_mode == "MULTI_PARENT")

    def update_status(self, job_id: str, status: JobStatus) -> None:
        self.db.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, now_iso(), job_id),
        )
        self.db.commit()

    def update_status_if_owned(
        self, job_id: str, worker_id: str, status: JobStatus
    ) -> bool:
        """Update runtime status only while this worker holds a live lease."""
        now = now_iso()
        cur = self.db.execute(
            """
            UPDATE jobs SET status = ?, updated_at = ?
            WHERE id = ? AND worker_id = ? AND worker_lease_until > ?
            """,
            (status.value, now, job_id, worker_id, now),
        )
        self.db.commit()
        return cur.rowcount == 1

    def set_requested_command(self, job_id: str, command: str) -> None:
        self.db.execute(
            "UPDATE jobs SET requested_command = ?, updated_at = ? WHERE id = ?",
            (command, now_iso(), job_id),
        )
        self.db.commit()

    def get_requested_command(self, job_id: str) -> str:
        row = self.db.execute(
            "SELECT requested_command FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return row["requested_command"] if row else "NONE"

    def clear_requested_command(self, job_id: str) -> None:
        self.set_requested_command(job_id, "NONE")

    def touch_heartbeat(self, job_id: str) -> None:
        self.db.execute(
            "UPDATE jobs SET worker_heartbeat_at = ?, updated_at = ? WHERE id = ?",
            (now_iso(), now_iso(), job_id),
        )
        self.db.commit()

    def _add_iso_seconds(self, seconds: int) -> str:
        from ..models import iso_from_offset

        return iso_from_offset(seconds)

    def claim_worker(
        self,
        job_id: str,
        worker_id: str,
        lease_seconds: int,
        auto_renew_from_now: bool = True,
        takeover_grace_seconds: int = 0,
    ) -> bool:
        """Atomically claim a job for this worker if it is not currently owned
        by a live worker (or the existing lease has expired past the takeover
        grace period). Returns True iff this worker won the claim."""
        now = now_iso()
        now_epoch = _to_epoch(now)
        lease_until = self._add_iso_seconds(lease_seconds)

        # Reject if another worker holds a live lease.
        row = self.db.execute(
            "SELECT worker_id, worker_lease_until FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is not None:
            current_worker = row["worker_id"]
            current_until = row["worker_lease_until"]
            if current_worker and current_worker != worker_id:
                # Only steal if the existing lease has expired *and* the grace
                # period has elapsed (P0-17: reduce mid-operation takeover risk).
                until_epoch = _to_epoch(current_until)
                if (
                    until_epoch is not None
                    and until_epoch + takeover_grace_seconds > now_epoch
                ):
                    return False

        # If this worker already owns it, treat as an (idempotent) renewal.
        if row is not None and row["worker_id"] == worker_id:
            cur = self.db.execute(
                """
                UPDATE jobs
                SET worker_heartbeat_at = ?, worker_lease_until = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, lease_until, now, job_id),
            )
            self.db.commit()
            return cur.rowcount == 1

        cur = self.db.execute(
            """
            UPDATE jobs
            SET worker_id = ?, worker_heartbeat_at = ?, worker_lease_until = ?,
                updated_at = ?
            WHERE id = ? AND (
                worker_id IS NULL
                OR worker_lease_until IS NULL
                OR worker_lease_until <= ?
            )
            """,
            (worker_id, now, lease_until, now, job_id, now),
        )
        self.db.commit()
        return cur.rowcount == 1

    def renew_worker(self, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        """Extend this worker's lease + heartbeat only if it still owns the job
        AND its lease has not already expired (P0-09: an expired lease can never
        be revived by a late heartbeat)."""
        now = now_iso()
        lease_until = self._add_iso_seconds(lease_seconds)
        cur = self.db.execute(
            """
            UPDATE jobs
            SET worker_heartbeat_at = ?, worker_lease_until = ?, updated_at = ?
            WHERE id = ? AND worker_id = ? AND worker_lease_until > ?
            """,
            (now, lease_until, now, job_id, worker_id, now),
        )
        self.db.commit()
        return cur.rowcount == 1

    def is_job_owned_by(
        self, job_id: str, worker_id: str, now: Optional[str] = None
    ) -> bool:
        """True iff THIS worker currently holds a live lease on the job.

        Ownership requires BOTH the worker_id to match AND the lease to be
        un-expired (P0-04). A matching ID with an expired lease is NOT owned."""
        effective_now = now or now_iso()
        row = self.db.execute(
            "SELECT worker_id, worker_lease_until FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None or row["worker_id"] != worker_id:
            return False
        until = _to_epoch(row["worker_lease_until"])
        if until is None:
            return False
        return until > _to_epoch(effective_now)

    def release_worker(self, job_id: str, worker_id: str) -> None:
        """Clear worker ownership/heartbeat/lease — but only if this worker is
        the current owner (so a freshly-taken-over worker isn't clobbered)."""
        self.db.execute(
            """
            UPDATE jobs
            SET worker_id = NULL, worker_heartbeat_at = NULL,
                worker_lease_until = NULL, updated_at = ?
            WHERE id = ? AND worker_id = ?
            """,
            (now_iso(), job_id, worker_id),
        )
        self.db.commit()

    def clear_worker_ownership(self, job_id: str) -> None:
        """Force-release worker ownership regardless of owner (used by stale
        recovery / resume)."""
        self.db.execute(
            """
            UPDATE jobs
            SET worker_id = NULL, worker_heartbeat_at = NULL,
                worker_lease_until = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now_iso(), job_id),
        )
        self.db.commit()

    # --- Account-level worker lease -----------------------------------------

    def claim_account(
        self,
        account_key: str,
        worker_id: str,
        job_id: str,
        lease_seconds: int,
        takeover_grace_seconds: int = 0,
    ) -> bool:
        """Atomically claim the Telegram account for this worker if no other
        live worker currently holds it. Returns True on success."""
        now = now_iso()
        now_epoch = _to_epoch(now)
        lease_until = self._add_iso_seconds(lease_seconds)

        row = self.db.execute(
            "SELECT worker_id, worker_lease_until FROM account_worker_state "
            "WHERE account_key = ?",
            (account_key,),
        ).fetchone()
        if row is not None:
            cur_worker = row["worker_id"]
            cur_until = row["worker_lease_until"]
            if cur_worker and cur_worker != worker_id:
                until_epoch = _to_epoch(cur_until)
                if (
                    until_epoch is not None
                    and until_epoch + takeover_grace_seconds > now_epoch
                ):
                    return False

        cur = self.db.execute(
            """
            INSERT INTO account_worker_state
                (account_key, worker_id, worker_heartbeat_at, worker_lease_until,
                 job_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_key) DO UPDATE SET
                worker_id = excluded.worker_id,
                worker_heartbeat_at = excluded.worker_heartbeat_at,
                worker_lease_until = excluded.worker_lease_until,
                job_id = excluded.job_id,
                updated_at = excluded.updated_at
            WHERE account_worker_state.worker_id IS NULL
               OR account_worker_state.worker_lease_until IS NULL
               OR account_worker_state.worker_lease_until <= ?
            """,
            (account_key, worker_id, now, lease_until, job_id, now, now),
        )
        self.db.commit()
        return cur.rowcount == 1

    def renew_account(
        self, account_key: str, worker_id: str, job_id: str, lease_seconds: int
    ) -> bool:
        """Extend the account lease only if this worker still owns it, still
        holds it for the SAME job, and its lease has not already expired
        (P0-10: an expired account lease can never be revived)."""
        now = now_iso()
        lease_until = self._add_iso_seconds(lease_seconds)
        cur = self.db.execute(
            """
            UPDATE account_worker_state
            SET worker_heartbeat_at = ?, worker_lease_until = ?, updated_at = ?
            WHERE account_key = ? AND worker_id = ? AND job_id = ?
              AND worker_lease_until > ?
            """,
            (now, lease_until, now, account_key, worker_id, job_id, now),
        )
        self.db.commit()
        return cur.rowcount == 1

    def is_account_owned_by(
        self,
        account_key: str,
        worker_id: str,
        job_id: str,
        now: Optional[str] = None,
    ) -> bool:
        """True iff THIS worker currently holds a live account lease for THIS
        job (P0-05): worker_id must match, job_id must match, and the lease must
        be un-expired."""
        effective_now = now or now_iso()
        row = self.db.execute(
            "SELECT worker_id, job_id, worker_lease_until FROM account_worker_state "
            "WHERE account_key = ?",
            (account_key,),
        ).fetchone()
        if row is None:
            return False
        if row["worker_id"] != worker_id or row["job_id"] != job_id:
            return False
        until = _to_epoch(row["worker_lease_until"])
        if until is None:
            return False
        return until > _to_epoch(effective_now)

    def release_account(
        self, account_key: str, worker_id: str, job_id: Optional[str] = None
    ) -> None:
        job_guard = " AND job_id = ?" if job_id is not None else ""
        params = [now_iso(), account_key, worker_id]
        if job_id is not None:
            params.append(job_id)
        self.db.execute(
            """
            UPDATE account_worker_state
            SET worker_id = NULL, worker_heartbeat_at = NULL,
                worker_lease_until = NULL, job_id = NULL, updated_at = ?
            WHERE account_key = ? AND worker_id = ?
            """ + job_guard,
            tuple(params),
        )
        self.db.commit()

    def has_live_account_lease_for_job(self, job_id: str) -> bool:
        row = self.db.execute(
            """
            SELECT 1 FROM account_worker_state
            WHERE job_id = ? AND worker_id IS NOT NULL
              AND worker_lease_until > ? LIMIT 1
            """,
            (job_id, now_iso()),
        ).fetchone()
        return row is not None

    def is_account_owned(self, account_key: str) -> bool:
        row = self.db.execute(
            "SELECT worker_id, worker_lease_until FROM account_worker_state "
            "WHERE account_key = ?",
            (account_key,),
        ).fetchone()
        if row is None:
            return False
        if not row["worker_id"]:
            return False
        until = _to_epoch(row["worker_lease_until"])
        if until is None or until <= _to_epoch(now_iso()):
            return False
        return True

    def has_unfinished_jobs_for_account(self, phone: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM jobs WHERE telegram_account_phone = ? "
            "AND status NOT IN (\'COMPLETED\', \'CANCELLED\') LIMIT 1",
            (phone,),
        ).fetchone()
        return row is not None

    def list_running_without_worker(self) -> List[str]:
        """Jobs marked RUNNING whose worker lease has expired (or never had a
        live lease). Used by stale-job recovery on startup."""
        now = now_iso()
        rows = self.db.execute(
            """
            SELECT id FROM jobs
            WHERE status IN ('RUNNING', 'RATE_LIMITED')
              AND (
                  worker_id IS NULL
                  OR worker_lease_until IS NULL
                  OR worker_lease_until <= ?
              )
            """,
            (now,),
        ).fetchall()
        return [r["id"] for r in rows]

    def record_job_error(
        self, job_id: str, error_type: str, error_message: Optional[str] = None
    ) -> None:
        self.db.execute(
            """
            UPDATE jobs
            SET last_error_type = ?, last_error_message = ?, updated_at = ?
            WHERE id = ?
            """,
            (error_type, error_message, now_iso(), job_id),
        )
        self.db.commit()

    def record_job_error_if_owned(
        self,
        job_id: str,
        worker_id: str,
        error_type: str,
        error_message: Optional[str] = None,
    ) -> bool:
        now = now_iso()
        cur = self.db.execute(
            """
            UPDATE jobs SET last_error_type = ?, last_error_message = ?,
                updated_at = ?
            WHERE id = ? AND worker_id = ? AND worker_lease_until > ?
            """,
            (error_type, error_message, now, job_id, worker_id, now),
        )
        self.db.commit()
        return cur.rowcount == 1

    def mark_started(self, job_id: str) -> None:
        self.db.execute(
            """
            UPDATE jobs
            SET status = ?, requested_command = 'NONE',
                started_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (JobStatus.RUNNING.value, now_iso(), now_iso(), job_id),
        )
        self.db.commit()

    def mark_started_if_owned(self, job_id: str, worker_id: str) -> bool:
        now = now_iso()
        cur = self.db.execute(
            """
            UPDATE jobs
            SET status = ?, requested_command = 'NONE', started_at = ?, updated_at = ?
            WHERE id = ? AND worker_id = ? AND worker_lease_until > ?
            """,
            (JobStatus.RUNNING.value, now, now, job_id, worker_id, now),
        )
        self.db.commit()
        return cur.rowcount == 1

    def mark_finished(self, job_id: str) -> None:
        self.db.execute(
            "UPDATE jobs SET status = ?, finished_at = ?, updated_at = ? WHERE id = ?",
            (JobStatus.COMPLETED.value, now_iso(), now_iso(), job_id),
        )
        self.db.commit()

    def mark_finished_if_owned(self, job_id: str, worker_id: str) -> bool:
        now = now_iso()
        cur = self.db.execute(
            """
            UPDATE jobs SET status = ?, finished_at = ?, updated_at = ?
            WHERE id = ? AND worker_id = ? AND worker_lease_until > ?
            """,
            (JobStatus.COMPLETED.value, now, now, job_id, worker_id, now),
        )
        self.db.commit()
        return cur.rowcount == 1

    def mark_cancelled_if_owned(self, job_id: str, worker_id: str) -> bool:
        now = now_iso()
        cur = self.db.execute(
            """
            UPDATE jobs SET status = ?, requested_command = 'NONE',
                pause_requested_at = NULL, paused_at = NULL,
                finished_at = ?, updated_at = ?
            WHERE id = ? AND worker_id = ? AND worker_lease_until > ?
            """,
            (JobStatus.CANCELLED.value, now, now, job_id, worker_id, now),
        )
        self.db.commit()
        return cur.rowcount == 1

    def mark_cancelled_if_unowned(self, job_id: str) -> bool:
        now = now_iso()
        cur = self.db.execute(
            """
            UPDATE jobs SET status = ?, requested_command = 'NONE',
                pause_requested_at = NULL, paused_at = NULL,
                finished_at = ?, updated_at = ?
            WHERE id = ? AND (worker_id IS NULL OR worker_lease_until IS NULL
                OR worker_lease_until <= ?)
            """,
            (JobStatus.CANCELLED.value, now, now, job_id, now),
        )
        self.db.commit()
        return cur.rowcount == 1

    def update_totals(
        self,
        job_id: str,
        total_items: int,
        processed_items: int,
        found_items: int,
        retry_items: int,
        failed_items: int,
        not_discoverable_items: int,
    ) -> None:
        self.db.execute(
            """
            UPDATE jobs
            SET total_items = ?,
                processed_items = ?,
                found_items = ?,
                retry_items = ?,
                failed_items = ?,
                not_discoverable_items = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                total_items,
                processed_items,
                found_items,
                retry_items,
                failed_items,
                not_discoverable_items,
                now_iso(),
                job_id,
            ),
        )
        self.db.commit()

    def reconcile_stats(self, job_id: str) -> None:
        row = self.db.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status IN ('FOUND','NOT_DISCOVERABLE','PERMANENT_ERROR','RETRY_REQUIRED','TEMPORARY_ERROR','RATE_LIMITED') THEN 1 ELSE 0 END) AS processed,
                SUM(CASE WHEN status = 'FOUND' THEN 1 ELSE 0 END) AS found,
                SUM(CASE WHEN status IN ('RETRY_REQUIRED','TEMPORARY_ERROR','RATE_LIMITED') THEN 1 ELSE 0 END) AS retry,
                SUM(CASE WHEN status = 'PERMANENT_ERROR' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status = 'NOT_DISCOVERABLE' THEN 1 ELSE 0 END) AS not_disc
            FROM check_items
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row and row["total"] is not None:
            self.update_totals(
                job_id,
                row["total"] or 0,
                row["processed"] or 0,
                row["found"] or 0,
                row["retry"] or 0,
                row["failed"] or 0,
                row["not_disc"] or 0,
            )

    def has_live_worker_lease(self, job_id: str) -> bool:
        """True if the job currently has an un-expired worker lease (i.e. a live
        worker owns it and may still be finishing a request)."""
        row = self.db.execute(
            "SELECT worker_id, worker_lease_until FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None or not row["worker_id"]:
            return False
        until = _to_epoch(row["worker_lease_until"])
        return until is not None and until > _to_epoch(now_iso())

    def set_pause_requested(self, job_id: str) -> None:
        self.db.execute(
            "UPDATE jobs SET requested_command = 'PAUSE', pause_requested_at = ?, "
            "updated_at = ? WHERE id = ?",
            (now_iso(), now_iso(), job_id),
        )
        self.db.commit()

    def acknowledge_pause(self, job_id: str, worker_id: str) -> None:
        """Record that a worker has honoured the pause and released its lease.
        Only the current owner (matching worker_id) may acknowledge, so a stale
        worker can't falsely ack for a new owner."""
        self.db.execute(
            "UPDATE jobs SET status = 'PAUSED', requested_command = 'NONE', "
            "paused_at = ?, worker_id = NULL, worker_heartbeat_at = NULL, "
            "worker_lease_until = NULL, updated_at = ? "
            "WHERE id = ? AND (worker_id IS NULL OR worker_id = ?)",
            (now_iso(), now_iso(), job_id, worker_id),
        )
        self.db.commit()

    def clear_pause_state(self, job_id: str) -> None:
        self.db.execute(
            "UPDATE jobs SET pause_requested_at = NULL, paused_at = NULL, "
            "updated_at = ? WHERE id = ?",
            (now_iso(), job_id),
        )
        self.db.commit()

    @staticmethod
    def _row_to_job(row) -> Job:
        def _col(name, default=None):
            try:
                return row[name]
            except (KeyError, IndexError):
                return default

        return Job(
            id=row["id"],
            name=row["name"],
            status=JobStatus(row["status"]),
            total_items=row["total_items"],
            processed_items=row["processed_items"],
            found_items=row["found_items"],
            retry_items=row["retry_items"],
            failed_items=row["failed_items"],
            not_discoverable_items=row["not_discoverable_items"],
            requested_command=_col("requested_command") or "NONE",
            worker_heartbeat_at=_col("worker_heartbeat_at"),
            worker_id=_col("worker_id"),
            worker_lease_until=_col("worker_lease_until"),
            last_error_type=_col("last_error_type"),
            last_error_message=_col("last_error_message"),
            pause_requested_at=_col("pause_requested_at"),
            paused_at=_col("paused_at"),
            worker_started_at=_col("worker_started_at"),
            telegram_account_phone=_col("telegram_account_phone"),
            job_mode=_col("job_mode", "SINGLE") or "SINGLE",
            parent_job_id=_col("parent_job_id"),
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            updated_at=row["updated_at"],
        )
