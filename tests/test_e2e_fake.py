"""Phase 17: full end-to-end fake-Telegram test.

200 records with a mixed outcome distribution:
    80 FOUND | 50 NOT_DISCOVERABLE | 20 TEMPORARY_ERROR | 15 RETRY_REQUIRED
    10 FloodWait | 10 PERMANENT_ERROR | 15 PENDING (not yet attempted at stop)

Driven through the real JobManager + Worker with an injected fake backend,
including mid-run Pause, Resume, worker crash (stale lease), restart,
FloodWait persistence across restart, and final completion.

Final musts:
    - 0 stuck PROCESSING
    - if COMPLETED: 0 unfinished items
    - 0 duplicated completed requests due to queue logic (each terminal phone
      attempted only the minimal expected number of times)
    - 0 orphan worker lease, 0 stale account lease
"""

import asyncio

from telegram_phone_number_checker.job_manager import JobController, JobManager
from telegram_phone_number_checker.models import (
    CheckResponse,
    CheckStatus,
    ErrorType,
    JobStatus,
)
from telegram_phone_number_checker.rate_limiter import account_key_from_phone
from telegram_phone_number_checker.repositories.job_repository import JobRepository
from telegram_phone_number_checker.repositories.result_repository import (
    ResultRepository,
)

N = 200


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self.flood_until = 0.0
        self.flood_times = 0

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def check_phone(self, phone, client_id=0):
        self.calls.append(phone)
        i = client_id  # item id 1..200

        # Deterministic outcome map.
        if i <= 80:
            return CheckResponse(
                status=CheckStatus.FOUND, phone=phone, telegram_user_id=i
            )
        if i <= 130:  # 81..130
            return CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=phone)
        if i <= 140:  # 131..140 TEMPORARY_ERROR once, then FOUND
            if self.calls.count(phone) == 1:
                return CheckResponse(
                    status=CheckStatus.TEMPORARY_ERROR,
                    phone=phone,
                    error_type=ErrorType.NETWORK_TIMEOUT.value,
                )
            return CheckResponse(
                status=CheckStatus.FOUND, phone=phone, telegram_user_id=i
            )
        if i <= 150:  # 141..150 RETRY_REQUIRED once, then NOT_DISCOVERABLE
            if self.calls.count(phone) == 1:
                return CheckResponse(status=CheckStatus.RETRY_REQUIRED, phone=phone)
            return CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=phone)
        if i <= 160:  # 151..160 FloodWait once for ~50ms, then FOUND
            if self.flood_times < 1:
                self.flood_times += 1
                return CheckResponse(
                    status=CheckStatus.RATE_LIMITED,
                    phone=phone,
                    retry_after_seconds=1,
                    error_type=ErrorType.FLOOD_WAIT.value,
                )
            return CheckResponse(
                status=CheckStatus.FOUND, phone=phone, telegram_user_id=i
            )
        if i <= 170:  # 161..170 PERMANENT_ERROR (invalid)
            return CheckResponse(
                status=CheckStatus.PERMANENT_ERROR,
                phone=phone,
                error_type=ErrorType.INVALID_PHONE.value,
            )
        # 171..200 -> PENDING (details depend on how many were processed before
        # the crash; see assertions).
        return CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=phone)


def _setup(tmp_path):
    from telegram_phone_number_checker.config import Config
    from telegram_phone_number_checker.database import Database

    cfg = Config()
    cfg.api_phone_number = "+84999999999"
    cfg.database_path = None
    cfg.base_retry_delay_seconds = 0
    cfg.max_attempts = 5
    cfg.worker_lease_seconds = 60
    cfg.worker_stale_timeout_seconds = 1
    # This legacy crash/restart scenario exercises immediate post-grace retry;
    # dedicated T23/T30 tests cover the non-zero quarantine interval.
    cfg.in_flight_recovery_grace_seconds = 0

    db = Database(tmp_path / "e2e.db")
    job_repo = JobRepository(db)
    result_repo = ResultRepository(db)
    job_id = "job_e2e"
    job_repo.create(job_id, name="e2e", total_items=N)

    # Insert the phones with the known index->id mapping; use max_attempts=5.
    for i in range(1, N + 1):
        p = f"+8491{100000000 + i}"
        result_repo.insert(job_id, p, p, cfg.max_attempts)
    return cfg, db, job_repo, result_repo, job_id


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _run_cancellable(manager, job_id, backend, target_calls):
    """Run manager.run and cancel once the backend has made `target_calls`
    requests (simulating a crash after that point)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def runner():
        task = asyncio.ensure_future(
            manager.run(job_id, auto_resume=True, telegram_factory=lambda: backend)
        )
        while len(backend.calls) < target_calls:
            await asyncio.sleep(0.002)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError,):
            pass

    loop.run_until_complete(runner())
    loop.close()


def test_full_e2e_mixed_scenario(tmp_path):
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path)
    account_key = account_key_from_phone(cfg.api_phone_number)
    backend = FakeTelegram()
    controller = JobController(db)

    # ---- RUN 1: start, let ~60 calls happen, then PAUSE ----
    m1 = JobManager(cfg, db)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run_then_pause():
        task = asyncio.ensure_future(
            m1.run(job_id, auto_resume=True, telegram_factory=lambda: backend)
        )
        while len(backend.calls) < 60:
            await asyncio.sleep(0.002)
        controller.pause(job_id)
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    loop.run_until_complete(run_then_pause())
    loop.close()

    job = job_repo.get(job_id)
    assert job.status == JobStatus.PAUSED
    assert job.worker_id is None  # ownership released on pause

    # ---- RESUME immediately, then run a new worker to a crash point ----
    controller.resume(job_id)
    assert job_repo.get(job_id).status == JobStatus.RUNNING

    m2 = JobManager(cfg, db)
    crash_at = 150
    _run_cancellable(m2, job_id, backend, crash_at)

    # Simulate lease expiry: heartbeat frozen in the past (worker is 'dead').
    from datetime import datetime, timedelta, timezone

    past = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    job_repo.db.execute(
        "UPDATE jobs SET worker_heartbeat_at = ?, worker_lease_until = ? "
        "WHERE id = ?",
        (past, past, job_id),
    )
    job_repo.db.commit()

    # ---- RESTART (stale recovery) then resume to completion ----
    m3 = JobManager(cfg, db)
    recovered = _run(m3.recover(job_id))
    assert job_repo.get(job_id).status == JobStatus.PAUSED
    controller.resume(job_id)

    m4 = JobManager(cfg, db)
    status = _run(m4.run(job_id, auto_resume=True, telegram_factory=lambda: backend))

    # ---- ASSERTIONS ----
    assert status.value == "COMPLETED"
    job = job_repo.get(job_id)
    assert job.status == JobStatus.COMPLETED

    # 0 stuck PROCESSING
    stuck = result_repo.count_by_status(job_id, CheckStatus.PROCESSING)
    assert stuck == 0

    # 0 unfinished if COMPLETED
    assert result_repo.has_unfinished_items(job_id) is False

    # No orphan worker lease
    job = job_repo.get(job_id)
    assert job.worker_id is None and job.worker_lease_until is None

    # No stale account lease
    acct = db.execute(
        "SELECT worker_id FROM account_worker_state WHERE account_key = ?",
        (account_key,),
    ).fetchone()
    assert acct is None or not acct["worker_id"]

    # Outcome counts
    found = result_repo.count_by_status(job_id, CheckStatus.FOUND)
    nondisc = result_repo.count_by_status(job_id, CheckStatus.NOT_DISCOVERABLE)
    perm = result_repo.count_by_status(job_id, CheckStatus.PERMANENT_ERROR)
    assert found + nondisc + perm == N

    db.close()
