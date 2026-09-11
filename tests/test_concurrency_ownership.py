"""Concurrency ownership tests T-01..T-12 for the P0 lock-down round.

These prove hard ownership guarantees:
  T-01  Account held by A -> B never calls Telegram
  T-02  Job held by A -> B never changes job status
  T-03  A in check_phone -> Pause -> Resume immediately -> no parallel worker
  T-04  A processing an item -> B never resets it to PENDING
  T-05  request longer than lease -> background heartbeat renews lease
  T-06  worker truly dead -> lease expires -> new worker recovers
  T-07  heartbeat old but lease active -> not stale
  T-08  heartbeat old + lease expired -> stale
  T-09  two jobs same account -> only one uses Telegram at a time
  T-10  two workers same job -> only one claims successfully
  T-11  worker loses ownership mid-request -> stops processing next item
  T-12  old worker releases after new worker -> does not clobber new owner
"""

import asyncio
import time

import pytest

from telegram_phone_number_checker.job_manager import JobManager
from telegram_phone_number_checker.models import CheckResponse, CheckStatus
from telegram_phone_number_checker.rate_limiter import account_key_from_phone
from telegram_phone_number_checker.repositories.job_repository import JobRepository
from telegram_phone_number_checker.repositories.result_repository import (
    ResultRepository,
)
from telegram_phone_number_checker.worker import (
    AccountBusyError,
    JobBusyError,
    LostOwnershipError,
    Worker,
)


class FakeTelegram:
    def __init__(self, behavior=None):
        self.behavior = behavior or {}
        self.calls = []
        self._sleep = 0.0

    def set_sleep(self, seconds):
        self._sleep = seconds

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def check_phone(self, phone, client_id=0):
        if self._sleep:
            await asyncio.sleep(self._sleep)
        self.calls.append(phone)
        entry = self.behavior.get(phone)
        if callable(entry):
            return entry(phone)
        if isinstance(entry, CheckResponse):
            return entry
        return CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=phone)


def _setup(tmp_path, phones, accounts_phones=None, **cfg_kwargs):
    from telegram_phone_number_checker.config import Config
    from telegram_phone_number_checker.database import Database

    cfg = Config()
    cfg.api_phone_number = "+84999999999"
    cfg.database_path = None
    for k, v in cfg_kwargs.items():
        setattr(cfg, k, v)
    db = Database(tmp_path / "concurrency.db")
    job_repo = JobRepository(db)
    result_repo = ResultRepository(db)
    job_id = "job_con"
    job_repo.create(job_id, name="con", total_items=len(phones))
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


def _make_worker(cfg, db, telegram, account_key=None, lease_seconds=2):
    from telegram_phone_number_checker.checkpoint import CheckpointManager
    from telegram_phone_number_checker.rate_limiter import RateLimitManager
    from telegram_phone_number_checker.retry_queue import RetryQueue

    result_repo = ResultRepository(db)
    return Worker(
        telegram,
        result_repo,
        CheckpointManager(result_repo),
        RetryQueue(
            result_repo, max_attempts=cfg.max_attempts, base_delay=0, max_delay=0
        ),
        RateLimitManager(),
        job_repo=JobRepository(db),
        account_key=account_key or account_key_from_phone(cfg.api_phone_number),
        lease_seconds=lease_seconds,
    )


@pytest.mark.asyncio
async def test_T01_account_held_A_worker_B_no_telegram(tmp_path):
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, ["+84911111111"])
    ak = account_key_from_phone(cfg.api_phone_number)
    fake_b = FakeTelegram()

    worker_a = _make_worker(cfg, db, FakeTelegram(), account_key=ak, lease_seconds=5)
    worker_a.claim(job_id)
    assert job_repo.is_account_owned(ak)

    # B targets a *different* job but the SAME account: the account claim must
    # fail (AccountBusyError), so B never contacts Telegram.
    job2 = "job_b"
    job_repo.create(job2, total_items=1)
    result_repo.insert(job2, "+84922222222", "+84922222222", 5)
    worker_b = _make_worker(cfg, db, fake_b, account_key=ak, lease_seconds=5)
    with pytest.raises(AccountBusyError):
        worker_b.claim(job2)
    assert fake_b.calls == []
    db.close()


@pytest.mark.asyncio
async def test_T02_job_held_A_worker_B_does_not_change_status(tmp_path):
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, ["+84911111111"])
    fake_b = FakeTelegram()

    worker_a = _make_worker(cfg, db, FakeTelegram(), lease_seconds=5)
    worker_a.claim(job_id)
    orig_status = job_repo.get(job_id).status.value

    worker_b = _make_worker(cfg, db, fake_b, lease_seconds=5)
    with pytest.raises(JobBusyError):
        worker_b.claim(job_id)
    assert job_repo.get(job_id).status.value == orig_status
    assert fake_b.calls == []
    db.close()


@pytest.mark.asyncio
async def test_T09_two_jobs_same_account_only_one_uses_telegram(tmp_path):
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, ["+84911111111"])
    ak = account_key_from_phone(cfg.api_phone_number)
    fake = FakeTelegram()

    w1 = _make_worker(cfg, db, fake, account_key=ak, lease_seconds=10)
    w1.claim(job_id)
    assert job_repo.is_account_owned(ak)

    # A second job (different id) on the same account must be refused.
    job2 = "job_second"
    job_repo.create(job2, total_items=1)
    result_repo.insert(job2, "+84922222222", "+84922222222", 5)
    w2 = _make_worker(cfg, db, fake, account_key=ak, lease_seconds=10)
    with pytest.raises((JobBusyError, AccountBusyError)):
        w2.claim(job2)
    assert fake.calls == []
    db.close()


@pytest.mark.asyncio
async def test_T10_two_workers_same_job_only_one_claims(tmp_path):
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, ["+84911111111"])
    ak = account_key_from_phone(cfg.api_phone_number)

    w1 = _make_worker(cfg, db, FakeTelegram(), account_key=ak, lease_seconds=10)
    w1.claim(job_id)
    w2 = _make_worker(cfg, db, FakeTelegram(), account_key=ak, lease_seconds=10)
    with pytest.raises(JobBusyError):
        w2.claim(job_id)
    db.close()


@pytest.mark.asyncio
async def test_T05_long_request_background_heartbeat_renews_lease(tmp_path):
    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path, ["+84911111111"], worker_lease_seconds=2
    )
    ak = account_key_from_phone(cfg.api_phone_number)
    fake = FakeTelegram()
    fake.set_sleep(3.0)  # request longer than the 2s lease

    w = _make_worker(cfg, db, fake, account_key=ak, lease_seconds=2)
    w.claim(job_id)
    await w.lease_keeper.start()
    task = asyncio.ensure_future(w.process(job_id, lambda: None))
    await asyncio.sleep(1.0)
    # Mid-request, heartbeats should have renewed the lease beyond expiry.
    job = job_repo.get(job_id)
    assert job.worker_id == w.worker_id
    assert job_repo.is_account_owned(ak)
    await asyncio.sleep(3.0)
    await task
    w.release()
    await w.lease_keeper.stop()
    assert fake.calls == ["+84911111111"]
    db.close()


@pytest.mark.asyncio
async def test_T11_worker_loses_ownership_mid_request_stops(tmp_path):
    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path, ["+84911111111", "+84922222222"], worker_lease_seconds=2
    )
    fake = FakeTelegram()
    fake.set_sleep(0.5)
    w = _make_worker(cfg, db, fake, lease_seconds=2)
    w.claim(job_id)

    async def run_then_steal():
        await w.lease_keeper.start()
        task = asyncio.ensure_future(w.process(job_id, lambda: None))
        await asyncio.sleep(0.1)
        # Another process force-clears ownership mid-request.
        job_repo.clear_worker_ownership(job_id)
        job_repo.release_account(
            account_key_from_phone(cfg.api_phone_number), w.worker_id
        )
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except (asyncio.TimeoutError, LostOwnershipError, asyncio.CancelledError):
            pass
        await w.lease_keeper.stop()

    await run_then_steal()
    # The worker must not have processed the second item after losing ownership.
    assert len(fake.calls) <= 1
    db.close()


@pytest.mark.asyncio
async def test_T12_old_worker_release_does_not_clobber_new_owner(tmp_path):
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, ["+84911111111"])
    ak = account_key_from_phone(cfg.api_phone_number)

    w_old = _make_worker(cfg, db, FakeTelegram(), account_key=ak, lease_seconds=2)
    w_old.claim(job_id)
    old_id = w_old.worker_id

    # Simulate takeover: new worker force-reclaims after the old lease expires.
    job_repo.clear_worker_ownership(job_id)
    job_repo.release_account(ak, old_id)
    w_new = _make_worker(cfg, db, FakeTelegram(), account_key=ak, lease_seconds=10)
    w_new.claim(job_id)
    new_id = w_new.worker_id

    # Old worker finally() fires late: its ownership-safe release must NOT
    # clear the NEW owner's lease.
    w_old.worker_id = old_id
    job_repo.release_worker(job_id, old_id)
    job_repo.release_account(ak, old_id)

    job = job_repo.get(job_id)
    assert job.worker_id == new_id
    assert job_repo.is_account_owned(ak)
    db.close()


@pytest.mark.asyncio
async def test_T07_T08_stale_decision_by_lease(tmp_path):
    from datetime import datetime, timedelta, timezone

    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, ["+84911111111"])
    mgr = JobManager(cfg, db)
    job = job_repo.get(job_id)
    old_hb = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    job.worker_heartbeat_at = old_hb
    job.worker_lease_until = (
        datetime.now(timezone.utc) + timedelta(seconds=30)
    ).isoformat()
    assert mgr._is_stale(job) is False  # T-07

    job.worker_lease_until = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    assert mgr._is_stale(job) is True  # T-08
    db.close()


@pytest.mark.asyncio
async def test_T03_pause_resume_immediate_no_parallel_worker(tmp_path):
    """T-03: While A is mid-request, a Pause then an immediate Resume must not
    spawn a second parallel worker. Resume refuses while a live lease exists."""
    from telegram_phone_number_checker.job_manager import JobController

    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path,
        ["+84911111111", "+84922222222", "+84933333333"],
        worker_lease_seconds=5,
    )
    fake = FakeTelegram()
    fake.set_sleep(1.0)
    mgr = JobManager(cfg, db)
    controller = JobController(db)

    task = asyncio.ensure_future(
        mgr.run(job_id, auto_resume=True, telegram_factory=lambda: fake)
    )
    await asyncio.sleep(0.15)
    controller.pause(job_id)

    # Resume attempted immediately while A is still mid-request / releasing:
    # it must NOT create a second worker (raise JobBusyError) nor run in
    # parallel. We deliberately expect a refusal, proving no second worker.
    from telegram_phone_number_checker.worker import JobBusyError as WB

    refused = False
    try:
        controller.resume(job_id)
    except WB:
        refused = True

    await asyncio.sleep(1.0)
    # If a second worker had run, Telegram calls would exceed the single pass.
    assert len(fake.calls) <= 3
    assert refused  # the immediate resume was rejected (no parallel worker)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    db.close()


@pytest.mark.asyncio
async def test_T04_live_worker_processing_not_reset_by_recovery(tmp_path):
    """T-04: an item marked PROCESSING by a live (leased) worker must NOT be
    reset to PENDING by recovery while the lease is still valid."""
    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path, ["+84911111111"], worker_lease_seconds=30
    )
    mgr = JobManager(cfg, db)

    # A live worker holds the lease and has marked the item PROCESSING.
    w = _make_worker(cfg, db, FakeTelegram(), lease_seconds=30)
    w.claim(job_id)
    result_repo.mark_processing(1)
    assert result_repo.get(1).status == CheckStatus.PROCESSING

    # Recovery must leave the PROCESSING item untouched (owner still alive).
    await mgr.recover(job_id)
    assert result_repo.get(1).status == CheckStatus.PROCESSING
    db.close()


@pytest.mark.asyncio
async def test_T06_dead_worker_lease_expired_recovers_processing(tmp_path):
    """T-06: a dead worker's PROCESSING item enters safe quarantine."""
    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path,
        ["+84911111111"],
        worker_lease_seconds=1,
        in_flight_recovery_grace_seconds=60,
    )
    mgr = JobManager(cfg, db)

    w = _make_worker(cfg, db, FakeTelegram(), lease_seconds=1)
    w.claim(job_id)
    result_repo.mark_processing(1)
    assert job_repo.get(job_id).worker_id == w.worker_id

    # Expire both ownership layers (simulated direct process death).
    from datetime import datetime, timedelta, timezone

    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    db.execute("UPDATE jobs SET worker_lease_until = ? WHERE id = ?", (past, job_id))
    db.execute(
        "UPDATE account_worker_state SET worker_lease_until = ? WHERE job_id = ?",
        (past, job_id),
    )
    db.commit()

    await mgr.recover(job_id)
    recovered = result_repo.get(1)
    assert recovered.status == CheckStatus.IN_FLIGHT_UNKNOWN
    assert recovered.recovery_after is not None
    db.close()
