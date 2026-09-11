import asyncio
import logging
import os
import sys
import traceback
from typing import Optional

from .checkpoint import CheckpointManager
from .config import Config
from .database import Database
from .logging_config import log_event
from .models import CheckStatus, JobStatus
from .rate_limiter import RateLimitManager, account_key_from_phone
from .repositories.job_repository import JobRepository
from .repositories.account_repository import AccountRepository
from .repositories.persistence_repository import SecretBox
from .telegram_session_store import SqlTelegramSessionStore
from .repositories.result_repository import ResultRepository
from .retry_queue import RetryQueue
from .telegram_service import TelegramService
from .worker import (
    AccountBusyError,
    ClaimError,
    JobBusyError,
    LostOwnershipError,
    Worker,
)

logger = logging.getLogger(__name__)


class JobManager:
    """Coordinates a job's lifecycle and its single worker."""

    def __init__(self, config: Config, db: Database):
        self._config = config
        self._db = db
        self._job_repo = JobRepository(db)
        self._result_repo = ResultRepository(db)
        self._checkpoint = CheckpointManager(self._result_repo)
        account_key = (
            account_key_from_phone(config.api_phone_number)
            if config.api_phone_number
            else None
        )
        self._rate_limiter = RateLimitManager(
            config.min_request_interval_seconds, db=db, account_key=account_key
        )
        self._retry_queue = RetryQueue(
            self._result_repo,
            max_attempts=config.max_attempts,
            base_delay=config.base_retry_delay_seconds,
            max_delay=config.max_retry_delay_seconds,
        )
        self._worker_done = asyncio.Event()
        self._worker_task: Optional[asyncio.Task] = None

    async def recover(self, job_id: str) -> None:
        """On startup, if a job is RUNNING but no live worker owns it (stale
        lease), release the orphan worker ownership and recover any interrupted
        items, then leave it PAUSED so the operator (or auto_resume) decides
        whether to continue."""
        job = self._job_repo.get(job_id)
        if job is None:
            return

        # P0-06: if ANY live worker currently holds a lease on this job, we must
        # not steal it, clear its ownership, or reset its in-flight PROCESSING
        # item. This guard is by lease (not the status label), so it holds even
        # if the job is labelled CREATED/PAUSED but a worker is mid-run.
        if self._job_repo.has_live_worker_lease(job_id):
            return
        # A split/still-live account lease is also an active owner.  Recovery
        # must wait until both job and account leases are dead.
        if self._job_repo.has_live_account_lease_for_job(job_id):
            return

        if job.status in (JobStatus.RUNNING, JobStatus.RATE_LIMITED):
            # Stale: no live worker holds a fresh lease -> force-release it and
            # recover interrupted items. This is the only safe path forward; an
            # actively-leased job must NOT be recovered here.
            account_key = self._config.api_phone_number and account_key_from_phone(
                self._config.api_phone_number
            )
            if account_key:
                self._job_repo.release_account(
                    account_key, job.worker_id or "stale", job_id
                )
            self._job_repo.clear_worker_ownership(job_id)
            self._job_repo.update_status(job_id, JobStatus.PAUSED)
            log_event(logger, "JOB_RECOVERED_TO_PAUSED", job_id=job_id)

        # Safe: no live worker holds a lease -> reset leftover PROCESSING items.
        recovered = self._checkpoint.recover_interrupted(
            job_id, self._config.in_flight_recovery_grace_seconds
        )
        if recovered > 0:
            log_event(
                logger, "RECOVERED_PROCESSING_ITEMS", job_id=job_id, count=recovered
            )

    async def recover_all_stale(self) -> int:
        """Scan all RUNNING/RATE_LIMITED jobs without a live lease, recover each
        (release ownership + items) to PAUSED. Returns how many were recovered."""
        n = 0
        for job_id in self._job_repo.list_running_without_worker():
            await self.recover(job_id)
            n += 1
        return n

    def _new_telegram_service(self) -> TelegramService:
        kwargs = {}
        master = os.getenv("PERSISTENCE_MASTER_KEY")
        phone = self._config.api_phone_number
        if getattr(self._db, "_use_postgres", False) and master and phone:
            if AccountRepository(self._db).get_by_phone(phone) is not None:
                store = SqlTelegramSessionStore(self._db, SecretBox(master))
                use_sql = store.has(phone) or not os.path.exists(f"{phone}.session")
                if use_sql:
                    kwargs["session_loader"] = lambda: store.load(phone)
                    kwargs["session_saver"] = lambda value: store.save(phone, value)
        return TelegramService(
            self._config.api_id, self._config.api_hash, phone,
            proxy=self._config.proxy, **kwargs
        )

    async def run(
        self,
        job_id: str,
        auto_resume: Optional[bool] = None,
        pause_on_finish: bool = False,
        telegram_factory=None,
    ) -> JobStatus:
        job = self._job_repo.get(job_id)
        if job is None:
            raise ValueError(f"Unknown job: {job_id}")

        if job.status in (JobStatus.RUNNING, JobStatus.RATE_LIMITED):
            if self._is_stale(job):
                # No live worker owns this job (heartbeat old/null): a fresh
                # worker may take over. Do not leave it RUNNING forever.
                pass
            else:
                # Another worker appears alive; refuse to double-run.
                raise JobBusyError(
                    f"Job {job_id} already has a live worker. Pause it first."
                )

        # recover any interrupted items (only when no live worker owns the job;
        # this is guaranteed here because run() refuses RUNNING+live-leased jobs)
        await self.recover(job_id)
        # Recovery may transition a stale RUNNING/RATE_LIMITED job to PAUSED.
        # Always reload the authoritative row before applying auto-resume policy.
        job = self._job_repo.get(job_id)
        if job is None:
            raise ValueError(f"Unknown job after recovery: {job_id}")
        # Load any persisted FloodWait cooldown from a previous run.
        self._rate_limiter.initialize()

        resume = self._config.auto_resume if auto_resume is None else auto_resume
        if job.status == JobStatus.PAUSED and not resume:
            raise JobPausedError(
                f"Job {job_id} is PAUSED. Resume it with 'job resume' or set AUTO_RESUME=true."
            )

        telegram = (
            telegram_factory()
            if telegram_factory is not None
            else self._new_telegram_service()
        )
        worker = Worker(
            telegram,
            self._result_repo,
            self._checkpoint,
            self._retry_queue,
            self._rate_limiter,
            job_repo=self._job_repo,
            account_key=self._rate_limiter._account_key,
            lease_seconds=self._config.worker_lease_seconds,
            renew_failure_limit=self._config.lease_renew_failure_limit,
            takeover_grace_seconds=self._config.lease_takeover_grace_seconds,
            in_flight_recovery_grace_seconds=(
                self._config.in_flight_recovery_grace_seconds
            ),
        )

        # P0-03: claim job + account BEFORE connecting Telegram. If either
        # claim fails we must NOT open a Telethon session / touch Telegram.
        try:
            worker.claim(job_id)
        except (JobBusyError, AccountBusyError) as exc:
            log_event(
                logger,
                "JOB_CLAIM_FAILED",
                job_id=job_id,
                worker_id=worker.worker_id,
                reason=str(exc),
            )
            raise exc

        if not self._job_repo.mark_started_if_owned(job_id, worker.worker_id):
            worker.release()
            raise LostOwnershipError(
                f"Worker {worker.worker_id} lost job {job_id} before start"
            )
        log_event(logger, "JOB_STARTED", job_id=job_id)

        # P0-01: start the lease keeper immediately after claim, BEFORE
        # telegram.connect()/OTP/2FA, so a long connect/login never lets the job
        # or account lease expire.
        try:
            await worker._start_lease_keeper()
        except Exception:
            worker.release()
            raise

        try:
            await telegram.connect()

            self._worker_done = asyncio.Event()
            self._worker_task = asyncio.ensure_future(
                worker.process(job_id, self._worker_done.set)
            )

            final_status = JobStatus.PAUSED
            try:
                # Wait for the worker to finish, keeping the job status live.
                while not self._worker_done.is_set():
                    self._job_repo.reconcile_stats(job_id)
                    # P0-05: if a pause has been requested (not yet acknowledged),
                    # do not overwrite status back to RUNNING/RATE_LIMITED. The
                    # worker's acknowledge_pause() sets the authoritative PAUSED.
                    if self._job_repo.get_requested_command(job_id) == "PAUSE":
                        pass
                    elif worker.paused_on_rate_limit:
                        if not self._job_repo.update_status_if_owned(
                            job_id, worker.worker_id, JobStatus.RATE_LIMITED
                        ):
                            raise LostOwnershipError(
                                f"Worker {worker.worker_id} cannot update job status"
                            )
                    else:
                        if not self._job_repo.update_status_if_owned(
                            job_id, worker.worker_id, JobStatus.RUNNING
                        ):
                            raise LostOwnershipError(
                                f"Worker {worker.worker_id} cannot update job status"
                            )
                    await asyncio.sleep(2.0)

                # Worker reported done. Re-raise any unexpected worker exception so
                # a crashed worker can NEVER produce a COMPLETED job.
                await self._worker_task
            except asyncio.CancelledError:
                worker.request_cancel()
                if self._worker_task is not None:
                    self._worker_task.cancel()
                    await asyncio.gather(self._worker_task, return_exceptions=True)
                self._job_repo.update_status_if_owned(
                    job_id, worker.worker_id, JobStatus.PAUSED
                )
                log_event(logger, "JOB_PAUSED_ON_SHUTDOWN", job_id=job_id)
                raise
            except LostOwnershipError:
                # The worker lost its lease mid-run (takeover/expiry). This is not
                # A stale worker must not mutate a job that may already belong
                # to a successor.  Stop local work and leave DB state untouched.
                worker.request_cancel()
                if self._worker_task is not None:
                    await asyncio.gather(self._worker_task, return_exceptions=True)
                log_event(
                    logger,
                    "JOB_LOST_OWNERSHIP",
                    job_id=job_id,
                    worker_id=worker.worker_id,
                )
                raise
            except Exception:
                # Worker raised an unexpected exception: mark FAILED and preserve
                # the checkpoint. Never COMPLETED.
                worker.request_cancel()
                if self._worker_task is not None:
                    await asyncio.gather(self._worker_task, return_exceptions=True)
                self._job_repo.update_status_if_owned(
                    job_id, worker.worker_id, JobStatus.FAILED
                )

                job = self._job_repo.get(job_id)
                if job is not None:
                    self._job_repo.record_job_error_if_owned(
                        job_id,
                        worker.worker_id,
                        "WORKER_CRASH",
                        traceback.format_exc()[-2000:],
                    )
                log_event(
                    logger,
                    "JOB_FAILED",
                    job_id=job_id,
                    error_type="WORKER_CRASH",
                )
                raise
            else:
                # Worker ended normally. Finalize: only COMPLETED if every item is
                # terminal; otherwise the job must remain recoverable (PAUSED).
                self._job_repo.reconcile_stats(job_id)
                job = self._job_repo.get(job_id)

                if worker.cancelled_by_command:
                    if not self._job_repo.mark_cancelled_if_owned(
                        job_id, worker.worker_id
                    ):
                        raise LostOwnershipError(
                            f"Worker {worker.worker_id} cannot cancel job {job_id}"
                        )
                    log_event(logger, "JOB_CANCELLED", job_id=job_id)
                    final_status = JobStatus.CANCELLED
                elif worker.paused_on_rate_limit:
                    self._job_repo.update_status_if_owned(
                        job_id, worker.worker_id, JobStatus.RATE_LIMITED
                    )
                    final_status = JobStatus.RATE_LIMITED
                elif not self._result_repo.has_unfinished_items(job_id):
                    if not self._job_repo.mark_finished_if_owned(
                        job_id, worker.worker_id
                    ):
                        raise LostOwnershipError(
                            f"Worker {worker.worker_id} cannot finish job {job_id}"
                        )
                    log_event(logger, "JOB_COMPLETED", job_id=job_id)
                    final_status = JobStatus.COMPLETED
                else:
                    # There are still items not fully settled (e.g. a retry was
                    # scheduled but the worker stopped for another reason). Do not
                    # mark COMPLETED; leave it PAUSED for a later resume.
                    # acknowledge_pause() may already have cleared the worker
                    # lease. In that case PAUSED is authoritative and no stale
                    # status write is needed.
                    current = self._job_repo.get(job_id)
                    if current is None or current.status != JobStatus.PAUSED:
                        self._job_repo.update_status_if_owned(
                            job_id, worker.worker_id, JobStatus.PAUSED
                        )
                    final_status = JobStatus.PAUSED
                return final_status
        finally:
            # P0-02: keep the lease keeper running THROUGH Telegram disconnect,
            # then stop it and release the leases only after the account is no
            # longer in use. P0-14: if connect() raised, this still releases the
            # leases (no orphan lease) and disconnect() is a safe no-op.
            had_active_exception = sys.exc_info()[0] is not None
            cleanup_errors = []
            try:
                await telegram.disconnect()
            except Exception as exc:  # noqa: BLE001 - cleanup must continue
                cleanup_errors.append(exc)
                log_event(
                    logger,
                    "DISCONNECT_FAILED",
                    job_id=job_id,
                    worker_id=worker.worker_id,
                    reason=f"{type(exc).__name__}: {exc}",
                )
            finally:
                try:
                    if worker.lease_keeper is not None:
                        await worker.lease_keeper.stop()
                except Exception as exc:  # noqa: BLE001
                    cleanup_errors.append(exc)
                    log_event(
                        logger,
                        "LEASE_KEEPER_STOP_FAILED",
                        job_id=job_id,
                        worker_id=worker.worker_id,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                finally:
                    try:
                        worker.release()
                    except Exception as exc:  # noqa: BLE001
                        cleanup_errors.append(exc)
            if cleanup_errors and not had_active_exception:
                raise cleanup_errors[0]

    def _is_stale(self, job) -> bool:
        """A RUNNING/RATE_LIMITED job is stale if its worker lease has expired
        (or was never held), meaning no live worker currently owns it.

        Ownership is decided by worker_lease_until, NOT the heartbeat: while the
        lease is still valid another worker must not recover the job even if the
        heartbeat looks old (a long request with a background heartbeat keeps
        the lease alive). Uses seconds_since_iso (a positive age).

        T-07: heartbeat old but lease active -> not stale.
        T-08: heartbeat old AND lease expired -> stale.
        """
        from .models import seconds_until_iso

        if job.worker_lease_until is None:
            # No lease at all -> nobody owns it -> stale.
            return True
        try:
            residual = seconds_until_iso(job.worker_lease_until)
        except Exception:
            return True
        # Lease still has time remaining -> a live owner -> not stale.
        return residual <= 0


class JobPausedError(RuntimeError):
    pass


class JobController:
    """Thin handler for pause/resume/status commands."""

    def __init__(self, db: Database):
        self._db = db
        self._job_repo = JobRepository(db)
        self._result_repo = ResultRepository(db)

    def status(self, job_id: str) -> dict:
        job = self._job_repo.get(job_id)
        if job is None:
            raise ValueError(f"Unknown job: {job_id}")
        pending = self._result_repo.count_pending_all_statuses(job_id)
        found = self._result_repo.count_by_status(job_id, CheckStatus.FOUND)
        not_disc = self._result_repo.count_by_status(
            job_id, CheckStatus.NOT_DISCOVERABLE
        )
        permanent = self._result_repo.count_by_status(
            job_id, CheckStatus.PERMANENT_ERROR
        )
        retrying = self._result_repo.count_by_status(job_id, CheckStatus.RETRY_REQUIRED)
        return {
            "job_id": job_id,
            "name": job.name,
            "status": job.status.value,
            "total": job.total_items,
            "processed": job.processed_items,
            "found": found,
            "not_discoverable": not_disc,
            "retry_queue": retrying,
            "errors": permanent,
            "pending": pending,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }

    def pause(self, job_id: str) -> None:
        """Persist a PAUSE request. The running worker polls this, finishes its
        current request, stops taking new items, releases its leases, then
        acknowledges the pause (status -> PAUSED + paused_at). We do NOT flip
        the status here so that a job with an un-acknowledged PAUSE keeps its
        RUNNING state; resume() will wait for the acknowledgement."""
        job = self._job_repo.get(job_id)
        if job is None:
            raise ValueError(f"Unknown job: {job_id}")
        if job.status == JobStatus.COMPLETED:
            raise ValueError(f"Job {job_id} is already COMPLETED")
        if job.status in (JobStatus.RUNNING, JobStatus.RATE_LIMITED):
            self._job_repo.set_pause_requested(job_id)
        else:
            # Not currently running: nothing to signal; mark paused directly.
            self._job_repo.update_status(job_id, JobStatus.PAUSED)
            self._job_repo.clear_pause_state(job_id)

    def resume(self, job_id: str) -> None:
        job = self._job_repo.get(job_id)
        if job is None:
            raise ValueError(f"Unknown job: {job_id}")
        if job.status == JobStatus.COMPLETED:
            raise ValueError(f"Job {job_id} is already COMPLETED")
        # P0-04: never spawn a new worker while the old worker is still holding
        # a live lease (pause not yet acknowledged). Wait isn't possible from a
        # sync CLI handler, so refuse clearly instead of racing.
        if self._job_repo.has_live_worker_lease(job_id):
            raise JobBusyError(
                f"Job {job_id} still has a live worker; pause not yet "
                "acknowledged. Wait a moment and resume again."
            )
        # The previous worker has released ownership (lease expired or cleared):
        # safe for a fresh worker to claim now.
        self._job_repo.clear_pause_state(job_id)
        self._job_repo.clear_requested_command(job_id)
        self._job_repo.mark_started(job_id)
