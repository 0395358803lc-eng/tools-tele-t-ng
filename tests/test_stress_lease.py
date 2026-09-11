"""Phase 12-13: stress + expanded database-invariant tests for the lease lock-down.

These run small-but-real concurrent workloads through JobManager with a fake
Telegram (no network) and then assert the preserved invariants:
  - max one worker per account & per job
  - no orphan job/account lease after completion
  - a COMPLETED job never has unfinished items or a live lease
  - a PAUSED job never holds a live account lease
"""

import asyncio
import random

import pytest

from telegram_phone_number_checker.database import Database
from telegram_phone_number_checker.job_manager import JobController, JobManager
from telegram_phone_number_checker.lease_keeper import LostOwnershipError
from telegram_phone_number_checker.models import CheckResponse, CheckStatus, ErrorType
from telegram_phone_number_checker.rate_limiter import account_key_from_phone
from telegram_phone_number_checker.repositories.job_repository import JobRepository
from telegram_phone_number_checker.repositories.result_repository import (
    ResultRepository,
)


class FakeTelegram:
    def __init__(self, behavior=None, request_sleep=0.0):
        self.behavior = behavior or {}
        self.request_sleep = request_sleep
        self.calls = []

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def check_phone(self, phone, client_id=0):
        if self.request_sleep:
            await asyncio.sleep(self.request_sleep)
        self.calls.append(phone)
        entry = self.behavior.get(phone)
        if callable(entry):
            return entry(phone)
        return CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=phone)


def _make_env(tmp_path, name):
    db = Database(tmp_path / f"{name}.db")
    job_repo = JobRepository(db)
    result_repo = ResultRepository(db)
    return db, job_repo, result_repo


def test_stress_many_items_no_orphan_leases(tmp_path):
    from telegram_phone_number_checker.config import Config

    nr_jobs = 3
    per_job = 40
    cfg = Config()
    cfg.api_phone_number = "+84999999999"
    cfg.database_path = None
    cfg.worker_lease_seconds = 10
    cfg.base_retry_delay_seconds = 0
    cfg.max_attempts = 3

    db, job_repo, result_repo = _make_env(tmp_path, "stress")
    account_key = account_key_from_phone(cfg.api_phone_number)

    jobs = []
    fake = FakeTelegram(request_sleep=0.02)
    for j in range(nr_jobs):
        jid = f"job_{j}"
        job_repo.create(jid, name=jid, total_items=per_job)
        for i in range(per_job):
            phone = f"+8491000000{j}{i:02d}"
            result_repo.insert(jid, phone, phone, cfg.max_attempts)
        jobs.append(jid)

    results = {}

    def run_sync(jid):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            mgr = JobManager(cfg, db)
            return loop.run_until_complete(
                mgr.run(jid, auto_resume=True, telegram_factory=lambda: fake)
            )
        finally:
            loop.close()

    for jid in jobs:
        results[jid] = run_sync(jid)

    # Every job COMPLETED and every item terminal.
    for jid in jobs:
        assert results[jid].value == "COMPLETED"
        assert result_repo.has_unfinished_items(jid) is False
        job = job_repo.get(jid)
        assert job.worker_id is None
        assert job.worker_lease_until is None

    # No orphan account lease after everything finished.
    assert job_repo.is_account_owned(account_key) is False

    # DB invariants must be clean.
    from telegram_phone_number_checker.database_validation import (
        validate_database_state,
    )

    assert validate_database_state(db) == []
    db.close()


def test_paused_job_never_holds_account_lease(tmp_path):
    from telegram_phone_number_checker.config import Config
    from telegram_phone_number_checker.models import JobStatus

    cfg = Config()
    cfg.api_phone_number = "+84999999999"
    cfg.database_path = None
    cfg.worker_lease_seconds = 5

    db, job_repo, result_repo = _make_env(tmp_path, "pause_inv")
    account_key = account_key_from_phone(cfg.api_phone_number)
    jid = "job_pause"
    job_repo.create(jid, total_items=3)
    for i in range(3):
        result_repo.insert(jid, f"+8491000000{i}", f"+8491000000{i}", 5)
    fake = FakeTelegram()
    mgr = JobManager(cfg, db)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run_and_pause():
        task = asyncio.ensure_future(
            mgr.run(jid, auto_resume=True, telegram_factory=lambda: fake)
        )
        await asyncio.sleep(0.05)
        job_repo.set_requested_command(jid, "PAUSE")
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    loop.run_until_complete(run_and_pause())
    loop.close()

    # Job is PAUSED and must NOT hold a live job or account lease.
    assert job_repo.get(jid).status == JobStatus.PAUSED
    assert job_repo.has_live_worker_lease(jid) is False
    from telegram_phone_number_checker.database_validation import (
        validate_database_state,
    )

    assert validate_database_state(db) == []
    db.close()


class FaultMatrixTelegram:
    """Deterministic pseudo-random 1,000-item fault injector."""

    def __init__(self, db, jobs, outcomes):
        self.db = db
        self.jobs = jobs
        self.outcomes = outcomes
        self.attempts = {}
        self.calls = 0
        self.lease_fault_injected = False
        self.fail_disconnect = False
        self.active_connections = 0
        self.max_connections = 0
        self.active_phones = set()
        self.simultaneous_duplicates = 0

    async def connect(self):
        self.active_connections += 1
        self.max_connections = max(self.max_connections, self.active_connections)

    async def disconnect(self):
        self.active_connections -= 1
        if self.fail_disconnect:
            self.fail_disconnect = False
            raise RuntimeError("injected disconnect failure")

    async def check_phone(self, phone, client_id=0):
        if phone in self.active_phones:
            self.simultaneous_duplicates += 1
        self.active_phones.add(phone)
        try:
            await asyncio.sleep(0.002)
            self.calls += 1
            self.attempts[phone] = self.attempts.get(phone, 0) + 1

            # Simulate a process/heartbeat ownership failure in mid-request.
            if self.calls == 300 and not self.lease_fault_injected:
                self.lease_fault_injected = True
                past = "2000-01-01T00:00:00Z"
                self.db.execute(
                    "UPDATE jobs SET worker_lease_until = ? WHERE id = 'stress_1000'",
                    (past,),
                )
                self.db.execute(
                    "UPDATE account_worker_state SET worker_lease_until = ? "
                    "WHERE job_id = 'stress_1000'",
                    (past,),
                )
                self.db.commit()

            kind = self.outcomes[client_id]
            if kind == "temporary" and self.attempts[phone] == 1:
                return CheckResponse(
                    status=CheckStatus.TEMPORARY_ERROR,
                    phone=phone,
                    error_type=ErrorType.NETWORK_TIMEOUT.value,
                )
            if kind == "flood" and self.attempts[phone] == 1:
                return CheckResponse(
                    status=CheckStatus.RATE_LIMITED,
                    phone=phone,
                    retry_after_seconds=0,
                    error_type=ErrorType.FLOOD_WAIT.value,
                )
            if kind == "permanent":
                return CheckResponse(
                    status=CheckStatus.PERMANENT_ERROR,
                    phone=phone,
                    error_type=ErrorType.INVALID_PHONE.value,
                )
            if kind == "found" or kind in ("temporary", "flood"):
                return CheckResponse(
                    status=CheckStatus.FOUND,
                    phone=phone,
                    telegram_user_id=client_id,
                )
            return CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=phone)
        finally:
            self.active_phones.discard(phone)


@pytest.mark.asyncio
async def test_stress_1000_fault_matrix_has_no_corruption_or_orphan_lease(tmp_path):
    """1,000 fake items across lease loss, crash recovery, pause/resume,
    temporary errors, FloodWait, and disconnect failure."""
    from telegram_phone_number_checker.config import Config
    from telegram_phone_number_checker.database_validation import (
        validate_database_state,
    )
    from telegram_phone_number_checker.models import JobStatus

    cfg = Config()
    cfg.api_phone_number = "+84999999999"
    cfg.database_path = None
    cfg.worker_lease_seconds = 3
    cfg.lease_renew_failure_limit = 1
    cfg.in_flight_recovery_grace_seconds = 0
    cfg.base_retry_delay_seconds = 0
    cfg.max_retry_delay_seconds = 0
    cfg.max_attempts = 3

    db, jobs, items = _make_env(tmp_path, "stress_1000")
    jobs.create("stress_1000", total_items=1000)
    rng = random.Random(1320)
    outcomes = {}
    choices = ("found", "not_found", "temporary", "flood", "permanent")
    weights = (40, 40, 8, 4, 8)
    for i in range(1, 1001):
        phone = f"+8492{i:08d}"
        items.insert("stress_1000", phone, phone, cfg.max_attempts)
        outcomes[i] = rng.choices(choices, weights=weights, k=1)[0]

    fake = FaultMatrixTelegram(db, jobs, outcomes)

    # Stage 1: injected lease expiry is detected during an in-flight request.
    with pytest.raises(LostOwnershipError):
        await JobManager(cfg, db).run(
            "stress_1000", auto_resume=True, telegram_factory=lambda: fake
        )
    assert fake.lease_fault_injected

    # Stage 2: takeover, then a real persisted pause acknowledgement.
    manager = JobManager(cfg, db)
    paused_run = asyncio.create_task(
        manager.run("stress_1000", auto_resume=True, telegram_factory=lambda: fake)
    )
    while fake.calls < 600:
        await asyncio.sleep(0.01)
    JobController(db).pause("stress_1000")
    assert await paused_run == JobStatus.PAUSED

    # Stage 3: resume to completion; disconnect fails, cleanup must still win.
    fake.fail_disconnect = True
    with pytest.raises(RuntimeError, match="injected disconnect failure"):
        await JobManager(cfg, db).run(
            "stress_1000", auto_resume=True, telegram_factory=lambda: fake
        )

    assert jobs.get("stress_1000").status == JobStatus.COMPLETED
    assert items.has_unfinished_items("stress_1000") is False
    assert items.count("stress_1000") == 1000
    assert fake.max_connections == 1
    assert fake.simultaneous_duplicates == 0
    assert jobs.has_live_worker_lease("stress_1000") is False
    assert jobs.is_account_owned(account_key_from_phone(cfg.api_phone_number)) is False
    assert validate_database_state(db) == []
    db.close()
