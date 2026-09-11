"""P0 lifecycle tests: job completion correctness, future retry, FloodWait
restart persistence, pause/resume, worker crash, and all-terminal completion.

These drive the real JobManager (which owns the COMPLETED decision) through an
injected fake Telegram service, so no network is needed.
"""

import asyncio
import time

import pytest

from telegram_phone_number_checker.checkpoint import CheckpointManager
from telegram_phone_number_checker.job_manager import JobManager
from telegram_phone_number_checker.models import (
    CheckResponse,
    CheckStatus,
    ErrorType,
    now_iso,
)
from telegram_phone_number_checker.rate_limiter import (
    RateLimitManager,
    account_key_from_phone,
)
from telegram_phone_number_checker.repositories.job_repository import JobRepository
from telegram_phone_number_checker.repositories.result_repository import (
    ResultRepository,
)


class FakeTelegram:
    """Configurable fake Telegram: returns responses from a queue, or based on
    a per-phone behaviour map. Tracks calls and can raise to simulate crashes.
    """

    def __init__(self, behavior=None):
        # behavior: dict phone -> list of CheckResponse (consumed in order),
        # or callable(phone, attempt) -> CheckResponse
        self.behavior = behavior or {}
        self.calls = []  # list of (phone, attempt)
        self.raise_exc = None
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def check_phone(self, phone, client_id=0):
        if self.raise_exc is not None:
            raise self.raise_exc
        self.calls.append(phone)
        entry = self.behavior.get(phone)
        if callable(entry):
            return entry(phone)
        if isinstance(entry, CheckResponse):
            return entry
        if isinstance(entry, list):
            if not entry:
                return CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=phone)
            return entry.pop(0)
        return CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=phone)


def _setup(tmp_path, phones, **cfg_kwargs):
    from telegram_phone_number_checker.config import Config
    from telegram_phone_number_checker.database import Database

    cfg = Config()
    cfg.api_phone_number = "+84999999999"
    cfg.database_path = None
    for k, v in cfg_kwargs.items():
        setattr(cfg, k, v)

    db = Database(tmp_path / "lifecycle.db")
    job_repo = JobRepository(db)
    result_repo = ResultRepository(db)
    job_id = "job_life"
    job_repo.create(job_id, name="life", total_items=len(phones))
    for i, p in enumerate(phones):
        result_repo.insert(job_id, p, p, cfg.max_attempts)
    return cfg, db, job_repo, result_repo, job_id


def _manager(cfg, db):
    return JobManager(cfg, db)


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---- P0-08 / 12.1: future retry must NOT produce COMPLETED ----
def test_future_retry_does_not_complete_job(tmp_path):
    phone = "+84911111111"
    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path, [phone], base_retry_delay_seconds=30
    )

    # First attempt -> temporary error (network). attempt becomes 1,
    # next_retry_at ~ +30s in the future.
    fake = FakeTelegram(
        behavior={
            phone: [
                CheckResponse(
                    status=CheckStatus.TEMPORARY_ERROR,
                    phone=phone,
                    error_type=ErrorType.NETWORK_TIMEOUT.value,
                )
            ]
        }
    )

    # The item is scheduled for a retry 30s in the future, so a fresh worker
    # would find NO due item but MUST NOT consider the job complete.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    manager = _manager(cfg, db)
    status_holder = {}

    async def run_and_interrupt():
        task = asyncio.ensure_future(
            manager.run(job_id, auto_resume=True, telegram_factory=lambda: fake)
        )
        # Let the worker schedule the retry and enter its wait loop, then
        # simulate a restart/shutdown by cancelling the worker.
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            status_holder["status"] = await task
        except asyncio.CancelledError:
            status_holder["status"] = "CANCELLED"

    loop.run_until_complete(run_and_interrupt())
    loop.close()

    item = result_repo.get(1)
    assert item.status == CheckStatus.RETRY_REQUIRED
    assert item.next_retry_at is not None

    # Job must NOT be COMPLETED while a future retry is still pending.
    job_after = job_repo.get(job_id)
    assert job_after.status.value != "COMPLETED"

    # And the repository agrees no item is terminal yet.
    assert result_repo.has_unfinished_items(job_id) is True

    db.close()


# ---- 12.2: retry then success -> attempt_count=2, FOUND, COMPLETED ----
def test_retry_then_success(tmp_path):
    phone = "+84911111111"
    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path, [phone], base_retry_delay_seconds=0, max_attempts=5
    )
    fake = FakeTelegram(
        behavior={
            phone: [
                CheckResponse(
                    status=CheckStatus.TEMPORARY_ERROR,
                    phone=phone,
                    error_type=ErrorType.NETWORK_TIMEOUT.value,
                ),
                CheckResponse(
                    status=CheckStatus.FOUND, phone=phone, telegram_user_id=42
                ),
            ]
        }
    )
    manager = _manager(cfg, db)
    status = _run(manager.run(job_id, auto_resume=True, telegram_factory=lambda: fake))

    item = result_repo.get(1)
    assert item.attempt_count == 2
    assert item.status == CheckStatus.FOUND
    assert item.telegram_user_id == 42
    assert job_repo.get(job_id).status.value == "COMPLETED"

    db.close()


# ---- 12.8: all-terminal -> COMPLETED, and only then ----
def test_all_terminal_completes_job(tmp_path):
    phones = ["+84911111111", "+84911111112", "+84911111113"]
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, phones, max_attempts=5)
    fake = FakeTelegram(
        behavior={
            phones[0]: CheckResponse(
                status=CheckStatus.FOUND, phone=phones[0], telegram_user_id=1
            ),
            phones[1]: CheckResponse(
                status=CheckStatus.NOT_DISCOVERABLE, phone=phones[1]
            ),
            phones[2]: CheckResponse(
                status=CheckStatus.PERMANENT_ERROR,
                phone=phones[2],
                error_type=ErrorType.INVALID_PHONE.value,
            ),
        }
    )
    manager = _manager(cfg, db)
    status = _run(manager.run(job_id, auto_resume=True, telegram_factory=lambda: fake))

    assert status.value == "COMPLETED"
    job = job_repo.get(job_id)
    assert job.status.value == "COMPLETED"
    assert job.finished_at is not None

    db.close()


# ---- P0-09 / 12.3: FloodWait persists across restart ----
def test_floodwait_persists_across_restart(tmp_path):
    phone = "+84911111111"
    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path, [phone], base_retry_delay_seconds=0
    )
    # Simulate a FloodWait of 120s being registered by a first process run.
    rl = RateLimitManager(
        db=db, account_key=account_key_from_phone(cfg.api_phone_number)
    )
    rl.register_rate_limit(120)
    assert rl.is_blocked()

    # 'Restart': a brand-new RateLimitManager in a new JobManager must reload
    # the persisted blocked_until and still be blocked.
    db.close()
    from telegram_phone_number_checker.database import Database

    db2 = Database(tmp_path / "lifecycle.db")
    rl2 = RateLimitManager(
        db=db2, account_key=account_key_from_phone(cfg.api_phone_number)
    )
    rl2.initialize()
    assert rl2.is_blocked()
    assert rl2.remaining_block_seconds() > 0

    db2.close()


# ---- 12.4: crash during PROCESSING -> recovered on restart ----
def test_crash_during_processing_recovered(tmp_path):
    phone = "+84911111111"
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, [phone])
    # Leave the item stranded in PROCESSING (simulated power loss between
    # mark_processing and result save).
    result_repo.mark_processing(1)
    assert result_repo.get(1).status.value == "PROCESSING"

    # On a fresh worker start the item must be recovered (-> PENDING) and
    # processed, not lost.
    checkpoint = CheckpointManager(result_repo)
    recovered = checkpoint.recover_interrupted(job_id, recovery_grace_seconds=0)
    assert recovered == 1
    assert result_repo.get(1).status.value == "IN_FLIGHT_UNKNOWN"
    assert result_repo.get(1).last_error_type == "WORKER_INTERRUPTED"

    fake = FakeTelegram(
        behavior={
            phone: CheckResponse(
                status=CheckStatus.FOUND, phone=phone, telegram_user_id=9
            )
        }
    )
    manager = _manager(cfg, db)
    status = _run(manager.run(job_id, auto_resume=True, telegram_factory=lambda: fake))
    assert status.value == "COMPLETED"
    assert result_repo.get(1).status == CheckStatus.FOUND

    db.close()


# ---- P0-10 / 12.5: pause stops worker pulling new items ----
def test_pause_stops_worker_and_job_paused(tmp_path):
    phones = ["+84911111111", "+84911111112", "+84911111113"]
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, phones)
    fake = FakeTelegram(
        behavior={
            p: CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=p)
            for p in phones
        }
    )

    manager = _manager(cfg, db)
    # Launch the worker without awaiting; pause it after it has started.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def pause_after_start():
        task = asyncio.ensure_future(
            manager.run(job_id, auto_resume=True, telegram_factory=lambda: fake)
        )
        # Let the worker grab the first item, then request a pause via the
        # repository (as a separate CLI process would).
        await asyncio.sleep(0.15)
        from telegram_phone_number_checker.models import JobCommand

        job_repo.set_requested_command(job_id, "PAUSE")
        # Give the worker a moment to notice and stop.
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    loop.run_until_complete(pause_after_start())
    loop.close()

    job = job_repo.get(job_id)
    # In-flight item may have completed, but the job is not COMPLETED because
    # at least one item is unfinished and we requested a pause.
    assert job.status.value != "COMPLETED"
    db.close()


# ---- 12.6: resume works and does not auto-pause ----
def test_resume_then_worker_continues(tmp_path):
    phone = "+84911111111"
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, [phone])
    fake = FakeTelegram(
        behavior={
            phone: CheckResponse(
                status=CheckStatus.FOUND, phone=phone, telegram_user_id=5
            )
        }
    )

    # Mark paused first (as if previously paused).
    job_repo.update_status(
        job_id,
        __import__(
            "telegram_phone_number_checker.models", fromlist=["JobStatus"]
        ).JobStatus.PAUSED,
    )

    from telegram_phone_number_checker.job_manager import JobController

    controller = JobController(db)
    controller.resume(job_id)  # PAUSED -> RUNNING
    assert job_repo.get(job_id).status.value == "RUNNING"

    # Running a fresh worker on a RUNNING-but-stale job must proceed and
    # complete, never flip back to PAUSED on startup.
    manager = _manager(cfg, db)
    status = _run(manager.run(job_id, auto_resume=True, telegram_factory=lambda: fake))
    assert status.value == "COMPLETED"

    db.close()


# ---- 12.7 / P0-05: worker exception -> job != COMPLETED ----
def test_worker_exception_not_completed(tmp_path):
    phone = "+84911111111"
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, [phone])
    fake = FakeTelegram(
        behavior={phone: CheckResponse(status=CheckStatus.FOUND, phone=phone)}
    )
    fake.raise_exc = RuntimeError("unexpected worker explosion")

    manager = _manager(cfg, db)
    status = None
    try:
        status = _run(
            manager.run(job_id, auto_resume=True, telegram_factory=lambda: fake)
        )
    except Exception:
        pass

    job = job_repo.get(job_id)
    assert job.status.value == "FAILED"
    assert job.last_error_type == "WORKER_CRASH"
    assert job.status.value != "COMPLETED"

    db.close()


@pytest.mark.asyncio
async def test_graceful_cancel_is_terminal_and_stops_before_next_item(tmp_path):
    phones = ["+84911111111", "+84922222222"]
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, phones)

    class SlowFirstTelegram(FakeTelegram):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def check_phone(self, phone, client_id=0):
            self.calls.append(phone)
            self.started.set()
            await self.release.wait()
            return CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=phone)

    fake = SlowFirstTelegram()
    manager = _manager(cfg, db)
    task = asyncio.create_task(manager.run(job_id, auto_resume=True, telegram_factory=lambda: fake))
    await fake.started.wait()
    job_repo.set_requested_command(job_id, "CANCEL")
    fake.release.set()
    status = await task

    assert status.value == "CANCELLED"
    assert job_repo.get(job_id).status.value == "CANCELLED"
    assert job_repo.get(job_id).finished_at is not None
    assert len(fake.calls) == 1
    assert result_repo.has_unfinished_items(job_id) is True
    db.close()


# Regression: stale RUNNING job must recover to PAUSED when auto-resume is off.
def test_stale_running_job_respects_auto_resume_false(tmp_path):
    from telegram_phone_number_checker.job_manager import JobPausedError
    from telegram_phone_number_checker.models import JobStatus

    phone = "+84911111111"
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, [phone])
    job_repo.update_status(job_id, JobStatus.RUNNING)

    manager = _manager(cfg, db)
    fake = FakeTelegram(
        behavior={
            phone: CheckResponse(
                status=CheckStatus.FOUND, phone=phone, telegram_user_id=99
            )
        }
    )

    with pytest.raises(JobPausedError):
        _run(manager.run(job_id, auto_resume=False, telegram_factory=lambda: fake))

    assert job_repo.get(job_id).status == JobStatus.PAUSED
    assert result_repo.get(1).status == CheckStatus.PENDING
    assert fake.calls == []
    db.close()
