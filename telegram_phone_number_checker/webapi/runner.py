"""Background job execution for the web UI.

``JobRunner`` wraps the existing job engine in asyncio tasks keyed by job_id so
the API can start/pause/resume/cancel jobs and stream their progress without
blocking a request. All write safety (leases, fencing tokens, checkpointing)
comes from the existing engine untouched.
"""

import asyncio
import copy
import logging
from typing import Dict, Optional

from ..config import Config
from ..database import Database
from ..models import JobCommand, JobStatus
from ..job_manager import JobManager, JobPausedError
from ..lease_keeper import LostOwnershipError
from ..worker import AccountBusyError, JobBusyError
from ..logging_config import log_event
from ..repositories.job_repository import JobRepository
from ..repositories.result_repository import ResultRepository
from .sse import SSEHub

logger = logging.getLogger(__name__)




def _friendly_job_error(exc: Exception) -> str:
    if isinstance(exc, AccountBusyError):
        return "Tài khoản Telegram đang được một job khác sử dụng."
    if isinstance(exc, JobBusyError):
        return "Job đang được một worker khác xử lý."
    if isinstance(exc, LostOwnershipError):
        return "Worker đã mất quyền sở hữu job; hệ thống dừng ghi để tránh trùng dữ liệu."
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return "Mất kết nối trong khi xử lý. Checkpoint vẫn được giữ để có thể tiếp tục."
    name = type(exc).__name__
    if "OperationalError" in name or "Database" in name or "Psycopg" in name:
        return "Database gặp lỗi trong khi xử lý; job chưa được đánh dấu hoàn tất."
    if name == "FloodWaitError":
        return "Telegram đang giới hạn tốc độ; job sẽ chờ theo FloodWait."
    return f"Job gặp lỗi runtime ({name})."

class AutomationError(RuntimeError):
    """A user-facing error from starting/managing an automated job."""


class JobRunner:
    def __init__(
        self,
        config: Config,
        db: Database,
        sse: SSEHub,
        monitor_interval_seconds: float = 2.0,
        account_manager=None,
    ):
        self._config = config
        self._db = db
        self._sse = sse
        self._account_manager = account_manager
        self._tasks: Dict[str, asyncio.Task] = {}
        self._monitor_interval = monitor_interval_seconds
        self._monitor_task: Optional[asyncio.Task] = None

    # ---- lifecycle --------------------------------------------------------

    async def start_monitor(self) -> None:
        """Recover stale jobs and start the progress monitor."""
        try:
            count = await self.recover_all_stale()
            if count:
                log_event(logger, "WEB_RECOVERED_STALE_JOBS", count=count)
        except Exception as exc:  # noqa: BLE001
            log_event(
                logger, "WEB_RECOVERY_FAILED", reason=f"{type(exc).__name__}: {exc}"
            )
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def shutdown(self) -> None:
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
            self._monitor_task = None
        for job_id, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
            self._tasks.clear()

    def _config_for_job(self, job_id: str) -> Config:
        cfg = copy.copy(self._config)
        job = JobRepository(self._db).get(job_id)
        if job is not None and job.telegram_account_phone:
            cfg.api_phone_number = job.telegram_account_phone
        elif self._account_manager is not None:
            cfg.api_phone_number = self._account_manager.default_phone()
        return cfg

    async def recover_all_stale(self) -> int:
        repo = JobRepository(self._db)
        count = 0
        for job_id in repo.list_running_without_worker():
            await JobManager(self._config_for_job(job_id), self._db).recover(job_id)
            count += 1
        return count

    # ---- task bookkeeping -------------------------------------------------

    def is_active(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        return task is not None and not task.done()

    def active_jobs(self) -> Dict[str, str]:
        return {
            job_id: "RUNNING" for job_id, task in self._tasks.items() if not task.done()
        }

    # ---- actions ----------------------------------------------------------

    def start(self, job_id: str, auto_resume: Optional[bool] = None) -> None:
        """Start a single job or all branches of a multi-account parent."""
        if self.is_active(job_id):
            raise AutomationError(f"Job {job_id} đang chạy.")
        job = JobRepository(self._db).get(job_id)
        if job is None:
            raise AutomationError(f"Không tìm thấy job {job_id}.")
        if job.job_mode == "MULTI_PARENT":
            task = asyncio.create_task(self._run_multi_parent(job_id, auto_resume))
        else:
            task = asyncio.create_task(self._run_job(job_id, auto_resume=auto_resume))
        self._tasks[job_id] = task

    def pause(self, job_id: str) -> None:
        from ..job_manager import JobController
        repo = JobRepository(self._db)
        job = repo.get(job_id)
        if job is None:
            raise AutomationError(f"Không tìm thấy job {job_id}.")
        targets = repo.list_children(job_id) if job.job_mode == "MULTI_PARENT" else [job]
        for target in targets:
            if target.status not in (JobStatus.COMPLETED, JobStatus.CANCELLED):
                JobController(self._db).pause(target.id)

    def resume(self, job_id: str) -> None:
        from ..job_manager import JobController
        repo = JobRepository(self._db)
        job = repo.get(job_id)
        if job is None:
            raise AutomationError(f"Không tìm thấy job {job_id}.")
        targets = repo.list_children(job_id) if job.job_mode == "MULTI_PARENT" else [job]
        for target in targets:
            if target.status == JobStatus.PAUSED:
                JobController(self._db).resume(target.id)
        self.start(job_id, auto_resume=True)

    async def cancel(self, job_id: str) -> None:
        repo = JobRepository(self._db)
        job = repo.get(job_id)
        if job is None:
            raise AutomationError(f"Không tìm thấy job {job_id}.")
        if job.status in (JobStatus.COMPLETED, JobStatus.CANCELLED):
            return
        if job.job_mode == "MULTI_PARENT":
            for child in repo.list_children(job_id):
                if child.status not in (JobStatus.COMPLETED, JobStatus.CANCELLED):
                    await self.cancel(child.id)
            repo.set_requested_command(job_id, JobCommand.CANCEL.value)
            await self._publish_job(job_id, event_type="job_cancel_requested")
            return

        # Persist the intent so the owning worker stops at a safe command poll
        # boundary instead of being force-cancelled mid Telegram request.
        repo.set_requested_command(job_id, JobCommand.CANCEL.value)
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            await self._publish_job(job_id, event_type="job_cancel_requested")
            return

        # A worker in another process may still hold the lease. Leave the
        # persisted CANCEL for that owner; never clobber a live successor.
        if repo.has_live_worker_lease(job_id):
            await self._publish_job(job_id, event_type="job_cancel_requested")
            return

        if not repo.mark_cancelled_if_unowned(job_id):
            raise AutomationError("Không thể hủy job vì ownership vừa thay đổi.")
        await self._publish_job(job_id, event_type="job_cancelled")

    # ---- internals --------------------------------------------------------

    async def _run_multi_parent(self, parent_id: str, auto_resume: Optional[bool]) -> None:
        repo = JobRepository(self._db)
        children = repo.list_children(parent_id)
        runnable = [c for c in children if c.status not in (JobStatus.COMPLETED, JobStatus.CANCELLED)]
        branch_tasks = []
        try:
            for child in runnable:
                if self.is_active(child.id):
                    continue
                task = asyncio.create_task(self._run_job(child.id, auto_resume=auto_resume))
                self._tasks[child.id] = task
                branch_tasks.append(task)
            if branch_tasks:
                await asyncio.gather(*branch_tasks, return_exceptions=True)
            children = repo.list_children(parent_id)
            statuses = [c.status for c in children]
            if statuses and all(s == JobStatus.COMPLETED for s in statuses):
                repo.mark_finished(parent_id)
            elif statuses and all(s == JobStatus.CANCELLED for s in statuses):
                repo.mark_cancelled_if_unowned(parent_id)
            elif any(s == JobStatus.PAUSED for s in statuses):
                repo.update_status(parent_id, JobStatus.PAUSED)
            elif any(s == JobStatus.FAILED for s in statuses):
                repo.update_status(parent_id, JobStatus.FAILED)
        finally:
            self._tasks.pop(parent_id, None)
            await self._publish_job(parent_id, event_type="job_update")

    async def _run_job(self, job_id: str, auto_resume: Optional[bool]) -> None:
        manager = JobManager(self._config_for_job(job_id), self._db)
        try:
            status = await manager.run(job_id, auto_resume=bool(auto_resume))
            log_event(logger, "WEB_JOB_FINISHED", job_id=job_id, status=status.value)
        except asyncio.CancelledError:
            log_event(logger, "WEB_JOB_CANCELLED", job_id=job_id)
            await self._publish_job(job_id, event_type="job_cancelled")
            raise
        except JobPausedError as exc:
            log_event(logger, "WEB_JOB_PAUSED", job_id=job_id, reason=str(exc))
            await self._sse.publish(
                {"type": "job_error", "job_id": job_id, "error": str(exc)}
            )
        except Exception as exc:  # noqa: BLE001
            log_event(
                logger,
                "WEB_JOB_FAILED",
                job_id=job_id,
                reason=f"{type(exc).__name__}: {exc}",
            )
            await self._sse.publish(
                {
                    "type": "job_error",
                    "job_id": job_id,
                    "error": _friendly_job_error(exc),
                    "error_type": type(exc).__name__,
                }
            )
        finally:
            self._tasks.pop(job_id, None)
            await self._publish_job(job_id, event_type="job_update")

    async def _publish_job(self, job_id: str, event_type: str = "job_update") -> None:
        data = self.job_summary(job_id)
        if data is not None:
            await self._sse.publish(
                {"type": event_type, "job_id": job_id, "data": data}
            )

    def job_summary(self, job_id: str) -> Optional[Dict]:
        from ..models import CheckStatus, mask_phone

        job_repo = JobRepository(self._db)
        result_repo = ResultRepository(self._db)
        job = job_repo.get(job_id)
        if job is None:
            return None
        requested = job_repo.get_requested_command(job_id)
        return {
            "job_id": job.id,
            "name": job.name,
            "status": job.status.value,
            "total": job.total_items,
            "processed": job.processed_items,
            "found": result_repo.count_by_status(job_id, CheckStatus.FOUND),
            "not_discoverable": result_repo.count_by_status(
                job_id, CheckStatus.NOT_DISCOVERABLE
            ),
            "retry_queue": result_repo.count_by_status(
                job_id, CheckStatus.RETRY_REQUIRED
            ),
            "errors": result_repo.count_by_status(job_id, CheckStatus.PERMANENT_ERROR),
            "pending": result_repo.count_pending_all_statuses(job_id),
            "active": self.is_active(job_id),
            "requested_command": requested,
            "telegram_account_phone": mask_phone(job.telegram_account_phone) if job.telegram_account_phone else None,
        }

    async def _monitor_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._monitor_interval)
                for job_id in list(self._tasks):
                    if self.is_active(job_id):
                        await self._publish_job(job_id, event_type="job_update")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log_event(
                logger, "WEB_MONITOR_ERROR", reason=f"{type(exc).__name__}: {exc}"
            )
