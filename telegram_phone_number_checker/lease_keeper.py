"""LeaseKeeper: owns lease renewal (heartbeat) for a single worker.

Extracted from the old inline worker heartbeat (P0-03) so that the keep-alive
loop is one coherent component and can be started BEFORE Telegram connects
(P0-01) and kept alive until AFTER Telegram disconnects (P0-02). It is the
single authority for "do I still own the job / account lease?" --
`ownership_valid` and `assert_ownership()` are the fail-closed gate the worker
and manager use before/after every Telegram interaction.

Policies implemented here:
  - P0-04 / P0-05 : ownership = worker_id match AND lease un-expired (plus
                    matching job_id for the account lease).
  - P0-09 / P0-10 : an expired lease can never be renewed (SQL guards).
  - P0-11 / P0-12 : heartbeat exceptions / renew failures are recorded, retried
                    up to LEASE_RENEW_FAILURE_LIMIT, then fail-closed.
  - P0-13        : sets an asyncio.Event so the processing loop wakes up and
                    stops taking new items the moment ownership is lost.
"""

import asyncio
import logging

from .logging_config import log_event

logger = logging.getLogger(__name__)


class LostOwnershipError(RuntimeError):
    """This worker no longer holds the job/account lease (renew failed or a
    takeover happened). It must stop immediately; it is not the active owner and
    must not mutate job/item state as if it were."""


class LeaseKeeper:
    def __init__(
        self,
        job_repo,
        job_id: str,
        worker_id: str,
        account_key,
        lease_seconds: int,
        renew_failure_limit: int = 3,
        interval_seconds: float | None = None,
    ):
        self._job_repo = job_repo
        self._job_id = job_id
        self._worker_id = worker_id
        self._account_key = account_key
        self._lease_seconds = lease_seconds
        self._renew_failure_limit = renew_failure_limit
        # Renew well before half-life so scheduler/DB jitter cannot let a short
        # lease expire between heartbeats. Keep an explicit interval override
        # untouched for deterministic tests.
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else min(max(lease_seconds / 4.0, 0.25), 10.0)
        )
        self._ownership_lost_event = asyncio.Event()
        self._renew_failures = 0
        self._heartbeat_task: asyncio.Task | None = None

    # ---- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Begin renewing job + account leases in the background (P0-01)."""
        if self._heartbeat_task is None or self._heartbeat_task.done():
            log_event(
                logger,
                "LEASE_KEEPER_STARTED",
                job_id=self._job_id,
                worker_id=self._worker_id,
                account_key=self._account_key or "",
            )
            self._heartbeat_task = asyncio.ensure_future(self._heartbeat_loop())

    async def stop(self) -> None:
        """Stop renewing leases (P0-02: call only AFTER Telegram disconnect)."""
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        log_event(
            logger,
            "LEASE_KEEPER_STOPPED",
            job_id=self._job_id,
            worker_id=self._worker_id,
        )

    # ---- ownership API --------------------------------------------------------

    @property
    def ownership_valid(self) -> bool:
        """False once renew has failed-closed or ownership was detected lost."""
        return not self._ownership_lost_event.is_set()

    @property
    def ownership_lost_event(self) -> asyncio.Event:
        return self._ownership_lost_event

    def assert_ownership(self) -> None:
        """Raise LostOwnershipError if this worker no longer owns the job or
        the account (by ID + live lease). P0-06/P0-07/P0-08."""
        if self._job_repo is None:
            # No ownership tracking configured (pure unit-test usage): nothing
            # to assert against.
            return
        if self._ownership_lost_event.is_set():
            raise LostOwnershipError(
                f"Worker {self._worker_id} lost ownership of job {self._job_id} "
                "(lease keeper failed closed)"
            )
        if not self._job_repo.is_job_owned_by(self._job_id, self._worker_id):
            self._mark_lost("JOB_OWNERSHIP_LOST")
            raise LostOwnershipError(
                f"Worker {self._worker_id} no longer owns job {self._job_id}"
            )
        if self._account_key and not self._job_repo.is_account_owned_by(
            self._account_key, self._worker_id, self._job_id
        ):
            self._mark_lost("ACCOUNT_OWNERSHIP_LOST")
            raise LostOwnershipError(
                f"Worker {self._worker_id} no longer owns account "
                f"{self._account_key} for job {self._job_id}"
            )

    async def wait_until_lost(self) -> None:
        """Wait until heartbeat failure, lease expiry, or takeover is seen."""
        await self._ownership_lost_event.wait()

    # ---- heartbeat -------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval)
                if self._ownership_lost_event.is_set():
                    return
                try:
                    ok = await self._renew_once()
                except Exception as exc:  # noqa: BLE001 - DB renew must fail-closed
                    log_event(
                        logger,
                        "LEASE_RENEW_FAILED",
                        job_id=self._job_id,
                        worker_id=self._worker_id,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                    ok = False

                if not ok:
                    self._renew_failures += 1
                    log_event(
                        logger,
                        "LEASE_RENEW_FAILED",
                        job_id=self._job_id,
                        worker_id=self._worker_id,
                        failure=self._renew_failures,
                        limit=self._renew_failure_limit,
                    )
                    if self._renew_failures >= self._renew_failure_limit:
                        # Fail-closed: we could not keep renewing reliably.
                        self._mark_lost("LEASE_EXPIRED")
                        return
                else:
                    self._renew_failures = 0
                    log_event(
                        logger,
                        "LEASE_RENEWED",
                        job_id=self._job_id,
                        worker_id=self._worker_id,
                        account_key=self._account_key or "",
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log_event(
                logger,
                "LEASE_KEEPER_ERROR",
                job_id=self._job_id,
                worker_id=self._worker_id,
                reason=f"{type(exc).__name__}: {exc}",
            )
            self._mark_lost("LEASE_KEEPER_ERROR")

    async def _renew_once(self) -> bool:
        ok = True
        if not self._job_repo.renew_worker(
            self._job_id, self._worker_id, self._lease_seconds
        ):
            ok = False
        if self._account_key and not self._job_repo.renew_account(
            self._account_key, self._worker_id, self._job_id, self._lease_seconds
        ):
            ok = False
        return ok

    def _mark_lost(self, event: str) -> None:
        self._ownership_lost_event.set()
        log_event(
            logger,
            event,
            job_id=self._job_id,
            worker_id=self._worker_id,
            account_key=self._account_key or "",
        )
