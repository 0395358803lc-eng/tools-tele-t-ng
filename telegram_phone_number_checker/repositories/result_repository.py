import uuid
from typing import Dict, List, Optional

from ..database import Database
from ..models import CheckItem, CheckStatus, ErrorType, now_iso


class ResultRepository:
    def __init__(self, db: Database):
        self.db = db

    _ITEM_COLUMNS = (
        "id, job_id, original_phone, normalized_phone, status, attempt_count, "
        "max_attempts, next_retry_at, last_error_type, last_error_message, "
        "telegram_user_id, username, first_name, last_name, user_was_online, "
        "cleanup_error, created_at, started_at, completed_at, "
        "in_flight_started_at, ownership_lost_at, recovery_after, "
        "processing_token, updated_at"
    )

    def insert(
        self, job_id: str, original_phone: str, normalized_phone: str, max_attempts: int
    ) -> CheckItem:
        now = now_iso()
        cur = self.db.execute(
            f"""
            INSERT INTO check_items (
                job_id, original_phone, normalized_phone, status, attempt_count,
                max_attempts, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?, ?)
            RETURNING id
            """,
            (
                job_id,
                original_phone,
                normalized_phone,
                CheckStatus.PENDING.value,
                max_attempts,
                now,
                now,
            ),
        )
        row = cur.fetchone()
        self.db.commit()
        return CheckItem(
            id=row["id"],
            job_id=job_id,
            original_phone=original_phone,
            normalized_phone=normalized_phone,
            status=CheckStatus.PENDING,
            max_attempts=max_attempts,
            created_at=now,
            updated_at=now,
        )

    def exists(self, job_id: str, normalized_phone: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM check_items WHERE job_id = ? AND normalized_phone = ? LIMIT 1",
            (job_id, normalized_phone),
        ).fetchone()
        return row is not None

    def get(self, item_id: int) -> Optional[CheckItem]:
        row = self.db.execute(
            f"SELECT {self._ITEM_COLUMNS} FROM check_items WHERE id = ?", (item_id,)
        ).fetchone()
        return self._row_to_item(row) if row else None

    def mark_processing(
        self,
        item_id: int,
        job_id: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> Optional[str]:
        """Atomically claim a selectable item and return its unique token.

        The token is the item-level fencing value.  A stale worker can only
        commit a result while this exact token is still present.
        """
        now = now_iso()
        token = uuid.uuid4().hex
        owner_guard = ""
        params = [CheckStatus.PROCESSING.value, now, now, token, now, item_id, now, now]
        if job_id is not None and worker_id is not None:
            owner_guard = (
                " AND job_id = ? AND EXISTS (SELECT 1 FROM jobs "
                "WHERE jobs.id = check_items.job_id AND jobs.worker_id = ? "
                "AND jobs.worker_lease_until > ?)"
            )
            params.extend((job_id, worker_id, now))
        cur = self.db.execute(
            """
            UPDATE check_items
            SET status = ?, started_at = ?, in_flight_started_at = ?,
                ownership_lost_at = NULL, recovery_after = NULL,
                processing_token = ?, updated_at = ?
            WHERE id = ? AND (
                (status IN ('PENDING','RETRY_REQUIRED','TEMPORARY_ERROR')
                 AND (next_retry_at IS NULL OR next_retry_at <= ?))
                OR
                (status = 'IN_FLIGHT_UNKNOWN'
                 AND recovery_after IS NOT NULL AND recovery_after <= ?)
            )
            """ + owner_guard,
            tuple(params),
        )
        self.db.commit()
        return token if cur.rowcount == 1 else None

    def save_result(
        self,
        item_id: int,
        status: CheckStatus,
        *,
        attempt_count: Optional[int] = None,
        next_retry_at: Optional[str] = None,
        last_error_type: Optional[str] = None,
        last_error_message: Optional[str] = None,
        telegram_user_id: Optional[int] = None,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        user_was_online: Optional[str] = None,
        cleanup_error: Optional[str] = None,
        completed: bool = False,
        processing_token: Optional[str] = None,
        job_id: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> bool:
        """Save a result, optionally guarded by item token and live job lease.

        Runtime workers always provide all three guard values.  Calls without
        them remain available for import/validation utilities and legacy unit
        tests that are not acting as an active worker.
        """
        completed_at = now_iso() if completed else None
        guard_sql = ""
        params_tail = [item_id]
        if processing_token is not None:
            guard_sql += " AND status = 'PROCESSING' AND processing_token = ?"
            params_tail.append(processing_token)
        if job_id is not None and worker_id is not None:
            guard_sql += (
                " AND job_id = ? AND EXISTS (SELECT 1 FROM jobs "
                "WHERE jobs.id = check_items.job_id AND jobs.worker_id = ? "
                "AND jobs.worker_lease_until > ?)"
            )
            params_tail.extend((job_id, worker_id, now_iso()))
        cur = self.db.execute(
            """
            UPDATE check_items
            SET status = ?,
                attempt_count = COALESCE(?, attempt_count),
                next_retry_at = ?,
                last_error_type = ?,
                last_error_message = ?,
                telegram_user_id = ?,
                username = ?,
                first_name = ?,
                last_name = ?,
                user_was_online = ?,
                cleanup_error = ?,
                completed_at = ?,
                processing_token = NULL,
                recovery_after = NULL,
                updated_at = ?
            WHERE id = ?
            """ + guard_sql,
            tuple(
                [
                    status.value,
                    attempt_count,
                    next_retry_at,
                    last_error_type,
                    last_error_message,
                    telegram_user_id,
                    username,
                    first_name,
                    last_name,
                    user_was_online,
                    cleanup_error,
                    completed_at,
                    now_iso(),
                ]
                + params_tail
            ),
        )
        self.db.commit()
        return cur.rowcount == 1

    def mark_in_flight_unknown(
        self, item_id: int, processing_token: str, recovery_after: str
    ) -> bool:
        """Quarantine only the PROCESSING claim still fenced by this token.

        This transition intentionally does not require a live job lease: it is
        the one containment write made *because* the lease was lost.  The token
        prevents it from touching an item already recovered by a new worker.
        """
        now = now_iso()
        cur = self.db.execute(
            """
            UPDATE check_items
            SET status = ?, ownership_lost_at = ?, recovery_after = ?,
                next_retry_at = NULL, processing_token = NULL,
                last_error_type = ?, last_error_message = ?, updated_at = ?
            WHERE id = ? AND status = 'PROCESSING' AND processing_token = ?
            """,
            (
                CheckStatus.IN_FLIGHT_UNKNOWN.value,
                now,
                recovery_after,
                ErrorType.WORKER_INTERRUPTED.value,
                "Ownership lost while Telegram request was in flight",
                now,
                item_id,
                processing_token,
            ),
        )
        self.db.commit()
        return cur.rowcount == 1

    def mark_retry(
        self,
        item_id: int,
        attempt_count: int,
        next_retry_at: str,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        processing_token: Optional[str] = None,
        job_id: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> bool:
        return self.save_result(
            item_id,
            CheckStatus.RETRY_REQUIRED,
            attempt_count=attempt_count,
            next_retry_at=next_retry_at,
            last_error_type=error_type,
            last_error_message=error_message,
            processing_token=processing_token,
            job_id=job_id,
            worker_id=worker_id,
        )

    def mark_permanent(
        self,
        item_id: int,
        error_type: str,
        error_message: Optional[str] = None,
    ) -> None:
        self.save_result(
            item_id,
            CheckStatus.PERMANENT_ERROR,
            last_error_type=error_type,
            last_error_message=error_message,
            completed=True,
        )

    def insert_invalid(
        self, job_id: str, original_phone: str, error_message: Optional[str] = None
    ) -> CheckItem:
        """Insert a phone that is permanently invalid (never sent to Telegram)
        directly as PERMANENT_ERROR."""
        now = now_iso()
        cur = self.db.execute(
            """
            INSERT INTO check_items (
                job_id, original_phone, normalized_phone, status, attempt_count,
                max_attempts, last_error_type, last_error_message, completed_at,
                created_at, updated_at
            ) VALUES (?, ?, NULL, ?, 0, 5, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                job_id,
                original_phone,
                CheckStatus.PERMANENT_ERROR.value,
                ErrorType.INVALID_PHONE.value,
                error_message or "Invalid phone number (not sent to Telegram)",
                now,
                now,
                now,
            ),
        )
        row = cur.fetchone()
        self.db.commit()
        return CheckItem(
            id=row["id"],
            job_id=job_id,
            original_phone=original_phone,
            normalized_phone=None,
            status=CheckStatus.PERMANENT_ERROR,
            last_error_type=ErrorType.INVALID_PHONE.value,
            last_error_message=error_message,
            created_at=now,
            updated_at=now,
        )

    def count_by_status(self, job_id: str, status: CheckStatus) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS c FROM check_items WHERE job_id = ? AND status = ?",
            (job_id, status.value),
        ).fetchone()
        return row["c"] if row else 0

    def has_unfinished_items(self, job_id: str) -> bool:
        """True if any item is still not in a terminal (settled) state.

        Terminal = FOUND / NOT_DISCOVERABLE / PERMANENT_ERROR. Anything in
        PENDING/PROCESSING/RETRY_REQUIRED/TEMPORARY_ERROR/RATE_LIMITED counts
        as unfinished and therefore blocks the job from being COMPLETED.
        """
        row = self.db.execute(
            """
            SELECT COUNT(*) AS c FROM check_items
            WHERE job_id = ?
              AND status NOT IN ('FOUND', 'NOT_DISCOVERABLE', 'PERMANENT_ERROR')
            """,
            (job_id,),
        ).fetchone()
        return (row["c"] if row else 0) > 0

    def get_next_retry_at(self, job_id: str) -> Optional[str]:
        """Earliest next_retry_at across still-pending retryable items, or
        None if there is none (e.g. only PENDING items with NULL next_retry_at
        remain)."""
        row = self.db.execute(
            """
            SELECT MIN(next_retry_at) AS next_retry_at
            FROM check_items
            WHERE job_id = ?
              AND status IN ('RETRY_REQUIRED', 'TEMPORARY_ERROR', 'RATE_LIMITED')
              AND next_retry_at IS NOT NULL
            """,
            (job_id,),
        ).fetchone()
        retry_at = row["next_retry_at"] if row else None
        recovery = self.db.execute(
            """
            SELECT MIN(recovery_after) AS recovery_after FROM check_items
            WHERE job_id = ? AND status = 'IN_FLIGHT_UNKNOWN'
              AND recovery_after IS NOT NULL
            """,
            (job_id,),
        ).fetchone()
        recovery_at = recovery["recovery_after"] if recovery else None
        candidates = [v for v in (retry_at, recovery_at) if v is not None]
        return min(candidates) if candidates else None

    def count_pending_all_statuses(self, job_id: str) -> int:
        row = self.db.execute(
            """
            SELECT COUNT(*) AS c FROM check_items
            WHERE job_id = ?
              AND (
                (status IN ('PENDING','RETRY_REQUIRED','TEMPORARY_ERROR')
                 AND (next_retry_at IS NULL OR next_retry_at <= ?))
                OR (status = 'IN_FLIGHT_UNKNOWN' AND recovery_after <= ?)
              )
            """,
            (job_id, now_iso(), now_iso()),
        ).fetchone()
        return row["c"] if row else 0

    def next_due_item(self, job_id: str) -> Optional[CheckItem]:
        now = now_iso()
        row = self.db.execute(
            """
            SELECT {cols} FROM check_items
            WHERE job_id = ?
              AND (
                (status IN ('PENDING','RETRY_REQUIRED','TEMPORARY_ERROR')
                 AND (next_retry_at IS NULL OR next_retry_at <= ?))
                OR (status = 'IN_FLIGHT_UNKNOWN' AND recovery_after <= ?)
              )
            ORDER BY id
            LIMIT 1
            """.format(cols=self._ITEM_COLUMNS),
            (job_id, now, now),
        ).fetchone()
        return self._row_to_item(row) if row else None

    def recover_interrupted(self, job_id: str, recovery_after: str) -> int:
        now = now_iso()
        cur = self.db.execute(
            """
            UPDATE check_items
            SET status = ?,
                last_error_type = ?,
                last_error_message = ?,
                next_retry_at = NULL,
                ownership_lost_at = ?,
                recovery_after = ?,
                processing_token = NULL,
                updated_at = ?
            WHERE job_id = ? AND status = ?
            """,
            (
                CheckStatus.IN_FLIGHT_UNKNOWN.value,
                ErrorType.WORKER_INTERRUPTED.value,
                "Worker stopped with an unresolved in-flight request",
                now,
                recovery_after,
                now,
                job_id,
                CheckStatus.PROCESSING.value,
            ),
        )
        self.db.commit()
        return cur.rowcount

    def all_items(self, job_id: str) -> List[Dict[str, Optional[object]]]:
        rows = self.db.execute(
            f"SELECT {self._ITEM_COLUMNS} FROM check_items WHERE job_id = ? ORDER BY id",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self, job_id: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS c FROM check_items WHERE job_id = ?", (job_id,)
        ).fetchone()
        return row["c"] if row else 0

    @staticmethod
    def _row_to_item(row) -> CheckItem:
        status = CheckStatus(row["status"]) if row["status"] else CheckStatus.PENDING
        return CheckItem(
            id=row["id"],
            job_id=row["job_id"],
            original_phone=row["original_phone"],
            normalized_phone=row["normalized_phone"],
            status=status,
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            next_retry_at=row["next_retry_at"],
            last_error_type=row["last_error_type"],
            last_error_message=row["last_error_message"],
            telegram_user_id=row["telegram_user_id"],
            username=row["username"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            user_was_online=row["user_was_online"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            in_flight_started_at=row["in_flight_started_at"],
            ownership_lost_at=row["ownership_lost_at"],
            recovery_after=row["recovery_after"],
            processing_token=row["processing_token"],
            updated_at=row["updated_at"],
        )
