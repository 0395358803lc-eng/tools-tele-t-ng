"""Phase 16 regression tests for the RC-stabilization work.

Covers:
  A. Heartbeat age -> stale detection (seconds_since_iso)
  B. Pause then resume immediately -> no JobBusyError, processing continues
  C. Worker crash + stale lease -> new worker can take over, PROCESSING recovered
  D. Two workers, one job -> only one wins the claim
  E. Two jobs, one Telegram account -> only one account lease active
  F. Persistent FloodWait cross-process -> second worker is blocked
"""

import asyncio
import time

from telegram_phone_number_checker.job_manager import JobController, JobManager
from telegram_phone_number_checker.models import (
    CheckResponse,
    CheckStatus,
    ErrorType,
    JobStatus,
    now_iso,
    seconds_since_iso,
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
    def __init__(self, behavior=None):
        self.behavior = behavior or {}
        self.calls = []
        self.raise_exc = None

    async def connect(self):
        pass

    async def disconnect(self):
        pass

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

    db = Database(tmp_path / "ownership.db")
    job_repo = JobRepository(db)
    result_repo = ResultRepository(db)
    job_id = "job_own"
    job_repo.create(job_id, name="own", total_items=len(phones))
    for i, p in enumerate(phones):
        result_repo.insert(job_id, p, p, cfg.max_attempts)
    return cfg, db, job_repo, result_repo, job_id


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------- A. Heartbeat age / stale detection ----------------


def test_seconds_since_iso_age():
    time.sleep(0.05)
    stamp = now_iso()
    # ~0s old -> not stale
    assert seconds_since_iso(stamp) < 1.0
    assert seconds_since_iso(None) == 0.0

    from datetime import datetime, timedelta, timezone

    past_31 = (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat()
    past_5m = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    assert seconds_since_iso(past_31) >= 31
    assert seconds_since_iso(past_5m) >= 300


def test_lease_stale_decision(tmp_path):
    """P0-10: staleness is decided by worker_lease_until, not the heartbeat.
    T-07: heartbeat old but lease active -> NOT stale.
    T-08: heartbeat old AND lease expired -> stale."""
    from datetime import datetime, timedelta, timezone

    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, ["+84911111111"])
    manager = JobManager(cfg, db)
    job = job_repo.get(job_id)

    # Null lease -> nobody owns it -> stale.
    job.worker_lease_until = None
    assert manager._is_stale(job) is True

    # Active lease even with a very old heartbeat -> NOT stale (T-07).
    old_hb = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    job.worker_heartbeat_at = old_hb
    job.worker_lease_until = (
        datetime.now(timezone.utc) + timedelta(seconds=30)
    ).isoformat()
    assert manager._is_stale(job) is False

    # Expired lease -> stale regardless of heartbeat (T-08).
    job.worker_lease_until = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    assert manager._is_stale(job) is True

    db.close()


# ---------------- B. Pause then Resume immediately ----------------


def test_pause_then_resume_immediately_no_busy(tmp_path):
    phones = ["+84911111111", "+84911111112", "+84911111113"]
    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path, phones, base_retry_delay_seconds=0
    )
    fake = FakeTelegram(
        behavior={
            p: CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=p)
            for p in phones
        }
    )
    controller = JobController(db)
    manager = JobManager(cfg, db)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Phase 1: start worker, pause it after it grabs the first item.
    async def _run_pause():
        task = asyncio.ensure_future(
            manager.run(job_id, auto_resume=True, telegram_factory=lambda: fake)
        )
        await asyncio.sleep(0.15)
        controller.pause(job_id)
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    loop.run_until_complete(_run_pause())

    job = job_repo.get(job_id)
    assert job.status == JobStatus.PAUSED
    # Ownership must have been released by the worker's finally.
    assert job.worker_id is None

    # Phase 2: resume immediately, then start a FRESH worker. Must NOT raise
    # JobBusyError and must complete all items.
    controller.resume(job_id)
    assert job_repo.get(job_id).status == JobStatus.RUNNING

    manager2 = JobManager(cfg, db)
    loop2 = asyncio.new_event_loop()
    asyncio.set_event_loop(loop2)
    try:
        status = loop2.run_until_complete(
            manager2.run(job_id, auto_resume=True, telegram_factory=lambda: fake)
        )
    finally:
        loop2.close()

    assert status.value == "COMPLETED"
    # No duplicate requests: each of 3 phones checked exactly once.
    assert sorted(fake.calls) == sorted(phones)
    for i in range(1, 4):
        assert result_repo.get(i).status == CheckStatus.NOT_DISCOVERABLE
    db.close()


# ---------------- C. Worker crash + stale lease -> takeover ----------------


def test_worker_crash_stale_lease_takeover(tmp_path):
    phones = ["+84911111111", "+84911111112"]
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, phones)
    cfg.in_flight_recovery_grace_seconds = 0
    # Simulate a worker that died: it claimed the job + account + marked an item
    # PROCESSING, then its lease expired (heartbeat/frozen in the past).
    dead_worker = "deadbeefdeadbeef"
    assert job_repo.claim_worker(job_id, dead_worker, lease_seconds=60)

    from datetime import datetime, timedelta, timezone

    past = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    job_repo.db.execute(
        "UPDATE jobs SET worker_heartbeat_at = ?, worker_lease_until = ? "
        "WHERE id = ?",
        (past, past, job_id),
    )
    job_repo.db.commit()
    result_repo.mark_processing(1)  # item stranded in PROCESSING

    # A fresh worker must be able to take over (stale lease) and recover the
    # PROCESSING item.
    fake = FakeTelegram(
        behavior={
            phones[0]: CheckResponse(status=CheckStatus.FOUND, phone=phones[0]),
            phones[1]: CheckResponse(
                status=CheckStatus.NOT_DISCOVERABLE, phone=phones[1]
            ),
        }
    )
    manager = JobManager(cfg, db)
    status = _run(manager.run(job_id, auto_resume=True, telegram_factory=lambda: fake))

    assert status.value == "COMPLETED"
    assert result_repo.get(1).status == CheckStatus.FOUND
    assert result_repo.get(2).status == CheckStatus.NOT_DISCOVERABLE
    db.close()


# ---------------- D. Two workers, one job -> only one wins ----------------


def test_two_workers_single_job_only_one_claims(tmp_path):
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, ["+84911111111"])
    w1, w2 = "worker_a" * 4, "worker_b" * 4  # 32 hex chars
    first = job_repo.claim_worker(job_id, w1, lease_seconds=60)
    second = job_repo.claim_worker(job_id, w2, lease_seconds=60)
    assert first is True
    assert second is False  # w1 holds a live lease
    job = job_repo.get(job_id)
    assert job.worker_id == w1
    db.close()


# ---------------- E. Two jobs, one account -> one account lease ----------------


def test_two_jobs_single_account_only_one_claims(tmp_path):
    from telegram_phone_number_checker.database import Database

    cfg = __import__(
        "telegram_phone_number_checker.config", fromlist=["Config"]
    ).Config()
    cfg.api_phone_number = "+84999999999"
    cfg.database_path = None

    db = Database(tmp_path / "account.db")
    job_repo = JobRepository(db)
    account_key = account_key_from_phone(cfg.api_phone_number)

    w1, w2 = "acct_a000000000000000", "acct_b000000000000000"
    ok1 = job_repo.claim_account(account_key, w1, "jobA", 60)
    ok2 = job_repo.claim_account(account_key, w2, "jobB", 60)
    assert ok1 is True
    assert ok2 is False  # account is busy with w1
    assert job_repo.is_account_owned(account_key) is True

    # Releasing should free it for a new owner.
    job_repo.release_account(account_key, w1)
    assert job_repo.is_account_owned(account_key) is False
    ok3 = job_repo.claim_account(account_key, w2, "jobB", 60)
    assert ok3 is True
    db.close()


# ---------------- F. Persistent FloodWait cross-process ----------------


def test_cross_process_floodwait_blocks_second_worker(tmp_path):
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, ["+84911111111"])
    account_key = account_key_from_phone(cfg.api_phone_number)
    rl1 = RateLimitManager(db=db, account_key=account_key)
    rl1.register_rate_limit(120)  # Job A hits FloodWait, writes DB
    assert rl1.is_blocked()

    # 'Process B': a brand-new RateLimitManager must see the persisted block
    # via a DB reload on acquire (no restart).
    rl2 = RateLimitManager(db=db, account_key=account_key)

    async def _acquire_b():
        # Use a short timeout guard so the test does not block 120s.
        task = asyncio.ensure_future(rl2.acquire())
        try:
            await asyncio.wait_for(task, timeout=0.5)
        except asyncio.TimeoutError:
            return "blocked"
        return "acquired"

    result = _run(_acquire_b())
    assert result == "blocked"  # process B waited because process A is blocked
    db.close()


# ---------------- G. Graceful shutdown (CTRL+C) releases leases ----------------


def test_graceful_shutdown_releases_leases(tmp_path):
    phones = ["+84911111111", "+84911111112", "+84911111113"]
    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path, phones, base_retry_delay_seconds=0
    )
    account_key = account_key_from_phone(cfg.api_phone_number)
    fake = FakeTelegram(
        behavior={
            p: CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=p)
            for p in phones
        }
    )
    manager = JobManager(cfg, db)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _shutdown():
        task = asyncio.ensure_future(
            manager.run(job_id, auto_resume=True, telegram_factory=lambda: fake)
        )
        # Let the worker claim + start processing, then simulate CTRL+C.
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    loop.run_until_complete(_shutdown())
    loop.close()

    job = job_repo.get(job_id)
    # Graceful shutdown leaves the job PAUSED (recoverable), not stuck RUNNING.
    assert job.status == JobStatus.PAUSED
    # No orphan worker lease.
    assert job.worker_id is None and job.worker_lease_until is None
    # No stale account lease.
    acct = db.execute(
        "SELECT worker_id FROM account_worker_state WHERE account_key = ?",
        (account_key,),
    ).fetchone()
    assert acct is None or not acct["worker_id"]
    db.close()
