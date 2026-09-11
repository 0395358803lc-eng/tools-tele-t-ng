import asyncio
import logging
import uuid
from typing import Optional

from .checkpoint import CheckpointManager
from .lease_keeper import LeaseKeeper, LostOwnershipError
from .logging_config import log_event
from .models import (
    CheckItem,
    CheckResponse,
    CheckStatus,
    JobCommand,
    iso_from_offset,
    seconds_until_iso,
)
from .rate_limiter import RateLimitManager
from .repositories.job_repository import JobRepository
from .repositories.result_repository import ResultRepository
from .retry_queue import RetryQueue
from .telegram_service import TelegramService

logger = logging.getLogger(__name__)


class ClaimError(RuntimeError):
    """Base for claim failures — distinct from a normal worker completion."""


class JobBusyError(ClaimError):
    """Another live worker already owns the job (or its lease is still valid)."""


class AccountBusyError(ClaimError):
    """Another live worker already holds this Telegram account's lease."""


class Worker:
    """Single Telegram worker per account/session (Phase 1: no concurrency).

    Ownership model (hard enforcement of the lease)
    ------------------------------------------------
    - A worker gets a unique worker_id (uuid) and must atomically CLAIM both
      the job and the Telegram account BEFORE the manager connects Telegram.
      claim() returns only after both are held; if either claim fails it raises
      a distinct ClaimError (JobBusyError / AccountBusyError) and the caller
      MUST NOT connect or touch Telegram.
    - A background heartbeat task renews BOTH the job worker lease and the
      account lease every ~lease/3 seconds for as long as the worker runs, so a
      long check_phone() request never lets the lease expire.
    - Every loop / before and after writes, the worker re-verifies it still
      owns a live lease. If the lease is lost (renew failed or takeover) it
      raises LostOwnershipError so it stops and never writes as the owner.
    - release_worker()/release_account() are ownership-safe: they only clear
      the lease if the worker_id still matches, so a stale worker can never
      clobber a newer owner.
    """

    def __init__(
        self,
        telegram: TelegramService,
        repo: ResultRepository,
        checkpoint: CheckpointManager,
        retry_queue: RetryQueue,
        rate_limiter: RateLimitManager,
        job_repo: Optional[JobRepository] = None,
        account_key: Optional[str] = None,
        lease_seconds: int = 60,
        renew_failure_limit: int = 3,
        takeover_grace_seconds: int = 0,
        in_flight_recovery_grace_seconds: int = 60,
    ):
        self._telegram = telegram
        self._repo = repo
        self._checkpoint = checkpoint
        self._retry = retry_queue
        self._rate = rate_limiter
        self._job_repo = job_repo
        self._account_key = account_key
        self._lease_seconds = lease_seconds
        self.worker_id = uuid.uuid4().hex
        self._job_id: Optional[str] = None
        self._cancel = asyncio.Event()
        self._command = JobCommand.NONE
        self._paused_on_rate_limit = False
        self._no_more_items = False
        self._retry_poll_interval = 5.0
        self._claimed_account = False
        self._paused_requested = False
        self._cancelled_by_command = False
        self._renew_failure_limit = renew_failure_limit
        self._takeover_grace_seconds = takeover_grace_seconds
        self._in_flight_recovery_grace_seconds = in_flight_recovery_grace_seconds
        self.lease_keeper: Optional[LeaseKeeper] = None

    def request_cancel(self) -> None:
        self._cancel.set()

    def set_command(self, command: JobCommand) -> None:
        self._command = command

    @property
    def paused_on_rate_limit(self) -> bool:
        return self._paused_on_rate_limit

    @property
    def cancelled_by_command(self) -> bool:
        return self._cancelled_by_command

    # ---- Claim (must happen BEFORE any Telegram connection) --------------

    def claim(self, job_id: str) -> None:
        """Atomically claim the job worker lease and the account lease. Raises
        JobBusyError / AccountBusyError on failure. Call this before connecting
        Telegram; on any ClaimError the caller must not proceed."""
        self._job_id = job_id
        log_event(logger, "WORKER_STARTED", job_id=job_id, worker_id=self.worker_id)

        if self._job_repo is not None and not self._job_repo.claim_worker(
            job_id,
            self.worker_id,
            self._lease_seconds,
            takeover_grace_seconds=self._takeover_grace_seconds,
        ):
            log_event(
                logger,
                "WORKER_CLAIM_FAILED_JOB_BUSY",
                job_id=job_id,
                worker_id=self.worker_id,
            )
            raise JobBusyError(
                f"Worker {self.worker_id} could not claim job {job_id}: another "
                "live worker owns it."
            )

        if (
            self._job_repo is not None
            and self._account_key
            and not self._job_repo.claim_account(
                self._account_key,
                self.worker_id,
                job_id,
                self._lease_seconds,
                takeover_grace_seconds=self._takeover_grace_seconds,
            )
        ):
            # Roll back the job claim we just won so we don't hold a lease we
            # can't use.
            self._job_repo.release_worker(job_id, self.worker_id)
            log_event(
                logger,
                "WORKER_CLAIM_FAILED_ACCOUNT_BUSY",
                job_id=job_id,
                worker_id=self.worker_id,
                account_key=self._account_key,
            )
            raise AccountBusyError(
                f"Worker {self.worker_id} could not claim account "
                f"{self._account_key} for job {job_id}: another worker holds it."
            )
        self._claimed_account = True
        self.lease_keeper = LeaseKeeper(
            self._job_repo,
            job_id,
            self.worker_id,
            self._account_key,
            self._lease_seconds,
            renew_failure_limit=self._renew_failure_limit,
        )
        log_event(
            logger,
            "LEASE_CLAIMED",
            job_id=job_id,
            worker_id=self.worker_id,
            account_key=self._account_key or "",
        )

    async def _start_lease_keeper(self) -> None:
        """Ensure the background lease keeper is running. P0-01: the manager
        starts it right after claim() and BEFORE telegram.connect(), so a long
        connect/OTP/2FA never lets the lease expire."""
        if self.lease_keeper is not None:
            await self.lease_keeper.start()

    def release(self) -> None:
        """Ownership-safe release of the job + account leases. P0-14/P0-02: the
        manager calls this AFTER telegram.disconnect() so the leases remain held
        for the whole Telegram lifecycle."""
        if self._job_repo is not None:
            errors = []
            # Account and job release are independent cleanup obligations.  An
            # exception in either must never prevent attempting the other.
            if self._claimed_account and self._account_key:
                try:
                    self._job_repo.release_account(
                        self._account_key, self.worker_id, self._job_id
                    )
                except Exception as exc:  # noqa: BLE001 - cleanup aggregation
                    errors.append(exc)
                    log_event(
                        logger,
                        "ACCOUNT_RELEASE_FAILED",
                        job_id=self._job_id or "",
                        worker_id=self.worker_id,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
            if self._job_id is not None:
                try:
                    self._job_repo.release_worker(self._job_id, self.worker_id)
                except Exception as exc:  # noqa: BLE001 - cleanup aggregation
                    errors.append(exc)
                    log_event(
                        logger,
                        "JOB_RELEASE_FAILED",
                        job_id=self._job_id,
                        worker_id=self.worker_id,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
            if not errors:
                log_event(
                    logger,
                    "LEASE_RELEASED",
                    job_id=self._job_id or "",
                    worker_id=self.worker_id,
                    account_key=self._account_key,
                )
            if errors:
                raise errors[0]

    # ---- Processing loop (run only after claim() + Telegram connect) ------

    async def process(self, job_id: str, done_callback) -> None:
        self._job_id = job_id
        try:
            if self._cancel.is_set():
                return
            while not self._cancel.is_set():
                self._poll_persisted_command(job_id)

                if self._command == JobCommand.CANCEL:
                    self._command = JobCommand.NONE
                    self._cancelled_by_command = True
                    log_event(
                        logger,
                        "WORKER_CANCELLED",
                        job_id=job_id,
                        worker_id=self.worker_id,
                    )
                    break
                if self._command == JobCommand.PAUSE:
                    self._command = JobCommand.NONE
                    self._paused_requested = True
                    log_event(
                        logger,
                        "WORKER_PAUSED",
                        job_id=job_id,
                        worker_id=self.worker_id,
                    )
                    break
                if self._command == JobCommand.RESUME:
                    self._command = JobCommand.NONE

                # Fail-closed ownership gate (P0-06/P0-07): if we no longer own
                # the job AND the account (by ID + live lease), raise instead of
                # silently dropping out, so the manager marks the job PAUSED and
                # never COMPLETED for a stolen job. A lost-owner worker must NOT
                # pull a new item or touch Telegram.
                self._assert_live_owner()

                if self._rate.is_blocked():
                    self._paused_on_rate_limit = True
                    log_event(
                        logger,
                        "WORKER_BLOCKED_BY_RATE",
                        job_id=job_id,
                        remaining_seconds=int(self._rate.remaining_block_seconds()),
                    )
                    await self._wait_for_rate_release()
                    continue
                self._paused_on_rate_limit = False

                item = self._checkpoint.next_item(job_id)
                if item is None:
                    if self._checkpoint.has_unfinished_items(job_id):
                        await self._wait_for_next_retry(job_id)
                        continue
                    log_event(
                        logger,
                        "WORKER_NO_MORE_ITEMS",
                        job_id=job_id,
                        worker_id=self.worker_id,
                    )
                    break

                await self._acquire_and_run_one(job_id, item)
        except asyncio.CancelledError:
            log_event(
                logger, "WORKER_CANCELLED", job_id=job_id, worker_id=self.worker_id
            )
            raise
        finally:
            # P0-05: pause is acknowledged only here, after the worker has
            # actually stopped pulling work. A stale worker can never ack for a
            # different (newer) owner. Leases are NOT released here: the manager
            # keeps them alive through Telegram disconnect (P0-02) and releases
            # them afterwards via worker.release().
            if self._job_repo is not None and self._paused_requested:
                self._job_repo.acknowledge_pause(job_id, self.worker_id)
                log_event(
                    logger,
                    "PAUSE_ACKNOWLEDGED",
                    job_id=job_id,
                    worker_id=self.worker_id,
                )
            self._no_more_items = True
            log_event(logger, "WORKER_STOPPED", job_id=job_id, worker_id=self.worker_id)
            done_callback()

    # ---- Ownership gate ------------------------------------------------------

    def _assert_live_owner(self) -> None:
        """Raise LostOwnershipError if this worker no longer owns the job/account
        lease (P0-07). Delegates to the LeaseKeeper's unified check (ID + expiry).
        If no LeaseKeeper exists (direct process() without claim()), ownership is
        untracked and treated as valid."""
        if self.lease_keeper is not None:
            self.lease_keeper.assert_ownership()

    def _poll_persisted_command(self, job_id: str) -> None:
        if self._job_repo is None:
            return
        raw = self._job_repo.get_requested_command(job_id)
        if raw and raw != JobCommand.NONE.value:
            self._command = JobCommand(raw)
            self._job_repo.clear_requested_command(job_id)

    async def _wait_for_rate_release(self) -> None:
        remaining = self._rate.remaining_block_seconds()
        await asyncio.sleep(min(remaining, 5.0) if remaining > 0 else 1.0)

    async def _wait_for_next_retry(self, job_id: str) -> None:
        next_retry_at = self._checkpoint.get_next_retry_at(job_id)
        wait = 0.0
        if next_retry_at is not None:
            wait = seconds_until_iso(next_retry_at)
        if wait <= 0:
            wait = 0.2
        wait = min(wait, self._retry_poll_interval)
        log_event(
            logger,
            "WORKER_WAITING_FOR_RETRY",
            job_id=job_id,
            wait_seconds=round(wait, 2),
        )
        await self._sleep_for(wait)

    async def _sleep_for(self, seconds: float) -> None:
        remaining = seconds
        while remaining > 0 and not self._cancel.is_set():
            slice_ = min(remaining, 1.0)
            await asyncio.sleep(slice_)
            remaining -= slice_

    async def _acquire_and_run_one(self, job_id: str, item: CheckItem) -> None:
        await self._rate.acquire()

        # Ownership-safe write: confirm we still own the lease BEFORE marking
        # this item PROCESSING and before hitting the network (P0-07). If we
        # lost the lease, stop — another worker owns this job now and must not
        # have its item reset/processed by us. NO Telegram request is sent.
        self._assert_live_owner()

        if self._job_repo is None:
            processing_token = self._checkpoint.mark_processing(item)
        else:
            processing_token = self._checkpoint.mark_processing(
                item, job_id, self.worker_id
            )
        if not processing_token:
            log_event(
                logger,
                "ITEM_TOKEN_MISMATCH",
                job_id=job_id,
                item_id=item.id,
                worker_id=self.worker_id,
            )
            return
        log_event(
            logger,
            "PHONE_CHECK_STARTED",
            job_id=job_id,
            item_id=item.id,
            phone=item.masked_phone,
            attempt=item.attempt_count + 1,
        )

        log_event(
            logger,
            "REQUEST_STARTED",
            job_id=job_id,
            item_id=item.id,
            phone=item.masked_phone,
            worker_id=self.worker_id,
        )

        request_task = asyncio.create_task(
            self._telegram.check_phone(
                item.normalized_phone or item.original_phone, client_id=item.id or 0
            )
        )
        ownership_task = None
        if self.lease_keeper is not None:
            ownership_task = asyncio.create_task(self.lease_keeper.wait_until_lost())

        try:
            if ownership_task is None:
                response = await request_task
            else:
                done, _ = await asyncio.wait(
                    (request_task, ownership_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # Ownership wins even if both tasks become ready in the same
                # event-loop turn.  A response cannot be committed after the
                # fencing event has fired.
                if ownership_task in done or not self.lease_keeper.ownership_valid:
                    if not request_task.done():
                        request_task.cancel()
                    await asyncio.gather(request_task, return_exceptions=True)
                    log_event(
                        logger,
                        "REQUEST_CANCELLED_OWNERSHIP_LOST",
                        job_id=job_id,
                        item_id=item.id,
                        phone=item.masked_phone,
                        worker_id=self.worker_id,
                    )
                    self._quarantine_in_flight(job_id, item, processing_token)
                    raise LostOwnershipError(
                        f"Ownership lost during request for item {item.id}"
                    )
                ownership_task.cancel()
                await asyncio.gather(ownership_task, return_exceptions=True)
                response = await request_task

            self._rate.register_success()

            # The task may finish just before a takeover.  Re-check the lease
            # after receiving the response and before the token-safe DB write.
            try:
                self._assert_live_owner()
            except LostOwnershipError:
                self._quarantine_in_flight(job_id, item, processing_token)
                raise

            await self._handle_response(job_id, item, response, processing_token)
        finally:
            if ownership_task is not None and not ownership_task.done():
                ownership_task.cancel()
                await asyncio.gather(ownership_task, return_exceptions=True)
            if not request_task.done():
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)

    def _quarantine_in_flight(
        self, job_id: str, item: CheckItem, processing_token: str
    ) -> bool:
        recovery_after = iso_from_offset(self._in_flight_recovery_grace_seconds)
        written = self._repo.mark_in_flight_unknown(
            item.id, processing_token, recovery_after
        )
        event = "IN_FLIGHT_UNKNOWN_CREATED" if written else "OLD_OWNER_WRITE_REJECTED"
        log_event(
            logger,
            event,
            job_id=job_id,
            item_id=item.id,
            worker_id=self.worker_id,
            recovery_after=recovery_after,
        )
        return written

    async def _handle_response(
        self,
        job_id: str,
        item: CheckItem,
        response: CheckResponse,
        processing_token: Optional[str] = None,
    ) -> None:
        status = response.status
        log_event(
            logger,
            "PHONE_CHECK_COMPLETED",
            job_id=job_id,
            item_id=item.id,
            phone=item.masked_phone,
            status=status.value,
            attempt=item.attempt_count + 1,
            error_type=response.error_type,
        )

        if status == CheckStatus.FOUND:
            written = self._repo.save_result(
                item.id,
                CheckStatus.FOUND,
                attempt_count=item.attempt_count + 1,
                telegram_user_id=response.telegram_user_id,
                username=response.username,
                first_name=response.first_name,
                last_name=response.last_name,
                user_was_online=response.user_was_online,
                cleanup_error=response.cleanup_error,
                completed=True,
                **self._write_guards(job_id, processing_token),
            )
            self._require_item_write(written, job_id, item)
            return

        if status == CheckStatus.NOT_DISCOVERABLE:
            written = self._repo.save_result(
                item.id,
                CheckStatus.NOT_DISCOVERABLE,
                attempt_count=item.attempt_count + 1,
                completed=True,
                **self._write_guards(job_id, processing_token),
            )
            self._require_item_write(written, job_id, item)
            return

        if status == CheckStatus.PERMANENT_ERROR:
            written = self._repo.save_result(
                item.id,
                CheckStatus.PERMANENT_ERROR,
                attempt_count=item.attempt_count + 1,
                last_error_type=response.error_type,
                last_error_message=response.error_message,
                completed=True,
                **self._write_guards(job_id, processing_token),
            )
            self._require_item_write(written, job_id, item)
            return

        if status == CheckStatus.RATE_LIMITED:
            seconds = (
                response.retry_after_seconds
                if response.retry_after_seconds is not None
                else 60
            )
            self._rate.register_rate_limit(seconds)
            self._paused_on_rate_limit = True
            written = self._retry.schedule_retry(
                item,
                error_type=response.error_type,
                error_message=response.error_message,
                retry_after_seconds=seconds,
                **self._write_guards(job_id, processing_token),
            )
            self._require_item_write(written, job_id, item)
            log_event(
                logger,
                "JOB_RATE_LIMITED",
                job_id=job_id,
                item_id=item.id,
                retry_after_seconds=seconds,
            )
            return

        # RETRY_REQUIRED or TEMPORARY_ERROR
        written = self._retry.schedule_retry(
            item,
            error_type=response.error_type,
            error_message=response.error_message,
            retry_after_seconds=response.retry_after_seconds,
            **self._write_guards(job_id, processing_token),
        )
        self._require_item_write(written, job_id, item)

    def _write_guards(self, job_id: str, processing_token: Optional[str]) -> dict:
        guards = {"processing_token": processing_token}
        if self._job_repo is not None:
            guards.update(job_id=job_id, worker_id=self.worker_id)
        return guards

    def _require_item_write(self, written: bool, job_id: str, item: CheckItem) -> None:
        if written:
            return
        log_event(
            logger,
            "OLD_OWNER_WRITE_REJECTED",
            job_id=job_id,
            item_id=item.id,
            worker_id=self.worker_id,
        )
        log_event(
            logger,
            "ITEM_TOKEN_MISMATCH",
            job_id=job_id,
            item_id=item.id,
            worker_id=self.worker_id,
        )
        raise LostOwnershipError(
            f"Item {item.id} no longer belongs to worker {self.worker_id}"
        )
