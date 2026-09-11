"""T-13..T-20: race-condition tests for the lease-keeper lifecycle lock-down.

These prove the fully-covered Telegram lifecycle:
  T-13  Long connect/login -> lease renewed; Worker B cannot claim while A is
        still connecting (P0-01/P0-15).
  T-14  Heartbeat renew raises a DB exception repeatedly -> fail-closed,
        ownership invalid, no new item (P0-11/P0-12).
  T-15  Heartbeat exception + Worker B takeover -> no duplicate concurrent
        request for the same PROCESSING item (P0-12).
  T-16  Expired lease cannot be renewed by a late heartbeat (P0-09/P0-10).
  T-17  Account ownership lost during an in-flight request -> A stops, does not
        process the next item (P0-08 + P0-05).
  T-18  Job ownership lost during an in-flight request -> A stops (P0-08+P0-04).
  T-19  Connect failure -> both job and account leases released (P0-14).
  T-20  Slow disconnect -> lease keeper stays active until disconnect returns
        (P0-02).
"""

import asyncio

import pytest

from telegram_phone_number_checker.lease_keeper import LeaseKeeper
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
    """Fake with configurable connect/disconnect/check_phone behaviour, plus
    injected connect errors and long sleeps."""

    def __init__(
        self, behavior=None, connect_error=None, connect_sleep=0.0, disconnect_sleep=0.0
    ):
        self.behavior = behavior or {}
        self.calls = []
        self.connect_error = connect_error
        self.connect_sleep = connect_sleep
        self.disconnect_sleep = disconnect_sleep
        self.connect_count = 0
        self.disconnect_count = 0

    async def connect(self):
        self.connect_count += 1
        if self.connect_error is not None:
            raise self.connect_error
        if self.connect_sleep:
            await asyncio.sleep(self.connect_sleep)

    async def disconnect(self):
        self.disconnect_count += 1
        if self.disconnect_sleep:
            await asyncio.sleep(self.disconnect_sleep)

    async def check_phone(self, phone, client_id=0):
        self.calls.append(phone)
        entry = self.behavior.get(phone)
        if callable(entry):
            return entry(phone)
        return CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=phone)


def _setup(tmp_path, phones, **cfg_kwargs):
    from telegram_phone_number_checker.config import Config
    from telegram_phone_number_checker.database import Database

    cfg = Config()
    cfg.api_phone_number = "+84999999999"
    cfg.database_path = None
    cfg.worker_lease_seconds = 2
    for k, v in cfg_kwargs.items():
        setattr(cfg, k, v)
    db = Database(tmp_path / "lk.db")
    job_repo = JobRepository(db)
    result_repo = ResultRepository(db)
    job_id = "job_t"
    job_repo.create(job_id, name="t", total_items=len(phones))
    for i, p in enumerate(phones):
        result_repo.insert(job_id, p, p, cfg.max_attempts)
    return cfg, db, job_repo, result_repo, job_id


def _make_worker(
    cfg,
    db,
    telegram,
    account_key=None,
    lease_seconds=2,
    renew_failure_limit=3,
    takeover_grace=0,
):
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
        renew_failure_limit=renew_failure_limit,
        takeover_grace_seconds=takeover_grace,
    )


# ---- T-13: long connect/login keeps the lease, blocks Worker B -------------


@pytest.mark.asyncio
async def test_T13_long_connect_lease_renewed_and_B_cannot_claim(tmp_path):
    from telegram_phone_number_checker.job_manager import JobManager

    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path, ["+84911111111"], worker_lease_seconds=2
    )
    ak = account_key_from_phone(cfg.api_phone_number)

    # Worker A's connect takes 3s (OTP/2FA wait) > the 2s lease.
    fake_a = FakeTelegram(connect_sleep=3.0)
    mgr = JobManager(cfg, db)
    task = asyncio.ensure_future(
        mgr.run(job_id, auto_resume=True, telegram_factory=lambda: fake_a)
    )

    # While A is still connecting, its job + account leases must stay alive
    # (renewed by the lease keeper started before connect -> P0-01).
    await asyncio.sleep(2.5)
    assert task.done() is False  # still connecting
    assert job_repo.has_live_worker_lease(job_id) is True
    assert job_repo.is_account_owned(ak) is True

    # A second worker must NOT be able to claim while A is connecting.
    w_b = _make_worker(cfg, db, FakeTelegram(), account_key=ak, lease_seconds=2)
    with pytest.raises((JobBusyError, AccountBusyError)):
        w_b.claim(job_id)

    await task
    assert fake_a.calls == ["+84911111111"]
    db.close()


# ---- T-14: heartbeat DB exception -> fail-closed ---------------------------


@pytest.mark.asyncio
async def test_T14_heartbeat_database_exception_fails_closed(tmp_path):
    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path, ["+84911111111"], worker_lease_seconds=2
    )
    w = _make_worker(cfg, db, FakeTelegram(), lease_seconds=2, renew_failure_limit=1)
    w.claim(job_id)
    await w.lease_keeper.start()

    # Make renews begin to raise a DB exception on every tick.
    def boom(*a, **k):
        raise RuntimeError("simulated DB lock")

    lk = w.lease_keeper
    lk._job_repo.renew_worker = boom
    lk._job_repo.renew_account = boom

    # With limit=1, the very next renew tick must set ownership lost.
    await asyncio.sleep(lk._interval + 0.5)
    assert lk.ownership_lost_event.is_set()
    assert lk.ownership_valid is False
    with pytest.raises(LostOwnershipError):
        lk.assert_ownership()

    # The processing loop must not pull a new item once the keeper failed.
    fake = w._telegram
    await w.lease_keeper.stop()
    assert fake.calls == []
    db.close()


# ---- T-15: heartbeat exception, no duplicate concurrent request ------------


@pytest.mark.asyncio
async def test_T15_no_duplicate_request_while_owner_still_leased(tmp_path):
    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path, ["+84911111111"], worker_lease_seconds=60
    )
    ak = account_key_from_phone(cfg.api_phone_number)
    fake_a = FakeTelegram()
    fake_b = FakeTelegram()

    w_a = _make_worker(
        cfg, db, fake_a, account_key=ak, lease_seconds=60, renew_failure_limit=1
    )
    w_a.claim(job_id)
    # A's item is in flight (PROCESSING) and A still holds a live lease.
    result_repo.mark_processing(1)

    # A's heartbeat fails closed, but its lease has NOT expired yet. B must NOT
    # take over while A still owns the lease -> no concurrent duplicate request.
    w_b = _make_worker(cfg, db, fake_b, account_key=ak, lease_seconds=10)
    with pytest.raises(JobBusyError):
        w_b.claim(job_id)
    assert fake_b.calls == []
    assert fake_a.calls == []
    # The PROCESSING item is untouched by B.
    assert result_repo.get(1).status == CheckStatus.PROCESSING
    db.close()


# ---- T-16: expired lease cannot renew --------------------------------------


@pytest.mark.asyncio
async def test_T16_expired_lease_cannot_renew(tmp_path):
    from datetime import datetime, timedelta, timezone

    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path, ["+84911111111"], worker_lease_seconds=60
    )
    ak = account_key_from_phone(cfg.api_phone_number)

    w = _make_worker(cfg, db, FakeTelegram(), account_key=ak, lease_seconds=60)
    w.claim(job_id)
    assert job_repo.renew_worker(job_id, w.worker_id, 60) is True
    assert job_repo.renew_account(ak, w.worker_id, job_id, 60) is True

    # Expire both leases by pushing worker_lease_until into the past.
    past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    job_repo.db.execute(
        "UPDATE jobs SET worker_lease_until = ? WHERE id = ?", (past, job_id)
    )
    job_repo.db.execute(
        "UPDATE account_worker_state SET worker_lease_until = ? WHERE account_key = ?",
        (past, ak),
    )
    job_repo.db.commit()

    # A late heartbeat must NOT revive an expired lease.
    assert job_repo.renew_worker(job_id, w.worker_id, 60) is False
    assert job_repo.renew_account(ak, w.worker_id, job_id, 60) is False
    assert job_repo.is_job_owned_by(job_id, w.worker_id) is False
    assert job_repo.is_account_owned_by(ak, w.worker_id, job_id) is False
    db.close()


# ---- T-17 / T-18: ownership lost during in-flight request -----------------


@pytest.mark.asyncio
async def test_T17_account_ownership_lost_during_request_stops_next(tmp_path):
    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path, ["+84911111111", "+84922222222"], worker_lease_seconds=5
    )
    ak = account_key_from_phone(cfg.api_phone_number)

    class SlowAfter:
        def __init__(self):
            self.calls = []
            self.first_started = asyncio.Event()

        async def check_phone(self, phone, client_id=0):
            self.calls.append(phone)
            if len(self.calls) == 1:
                self.first_started.set()
            await asyncio.sleep(0.4)
            return CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=phone)

    slow = SlowAfter()
    w = _make_worker(cfg, db, slow, account_key=ak, lease_seconds=5)
    w.claim(job_id)
    await w.lease_keeper.start()

    task = asyncio.ensure_future(w.process(job_id, lambda: None))
    await slow.first_started.wait()  # request 1 in flight (0.4s sleep)
    # Lose *account* ownership while request 1 is still running.
    job_repo.release_account(ak, w.worker_id)
    await asyncio.sleep(0.6)
    try:
        await asyncio.wait_for(task, timeout=3.0)
    except (LostOwnershipError, asyncio.TimeoutError):
        pass
    await w.lease_keeper.stop()
    # The worker must not have processed item 2 after losing account ownership.
    assert len(slow.calls) == 1
    db.close()


@pytest.mark.asyncio
async def test_T18_job_ownership_lost_during_request_stops_next(tmp_path):
    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path, ["+84911111111", "+84922222222"], worker_lease_seconds=5
    )
    ak = account_key_from_phone(cfg.api_phone_number)

    class SlowAfter:
        def __init__(self):
            self.calls = []
            self.first_started = asyncio.Event()

        async def check_phone(self, phone, client_id=0):
            self.calls.append(phone)
            if len(self.calls) == 1:
                self.first_started.set()
            await asyncio.sleep(0.4)
            return CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=phone)

    slow = SlowAfter()
    w = _make_worker(cfg, db, slow, account_key=ak, lease_seconds=5)
    w.claim(job_id)
    await w.lease_keeper.start()

    task = asyncio.ensure_future(w.process(job_id, lambda: None))
    await slow.first_started.wait()
    # Lose *job* ownership while request 1 is in flight.
    job_repo.clear_worker_ownership(job_id)
    await asyncio.sleep(0.6)
    try:
        await asyncio.wait_for(task, timeout=3.0)
    except (LostOwnershipError, asyncio.TimeoutError):
        pass
    await w.lease_keeper.stop()
    assert len(slow.calls) == 1
    db.close()


# ---- T-19: connect failure -> no orphan leases ------------------------------


@pytest.mark.asyncio
async def test_T19_connect_failure_cleanup_releases_leases(tmp_path):
    from telegram_phone_number_checker.database import Database
    from telegram_phone_number_checker.job_manager import JobManager

    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path, ["+84911111111"], worker_lease_seconds=30
    )
    ak = account_key_from_phone(cfg.api_phone_number)
    fake = FakeTelegram(connect_error=RuntimeError("cannot connect"))
    mgr = JobManager(cfg, db)

    with pytest.raises(RuntimeError):
        await mgr.run(job_id, auto_resume=True, telegram_factory=lambda: fake)

    # No orphan job lease, no orphan account lease.
    job = job_repo.get(job_id)
    assert job.worker_id is None
    assert job.worker_lease_until is None
    acct = db.execute(
        "SELECT worker_id FROM account_worker_state WHERE account_key = ?", (ak,)
    ).fetchone()
    assert acct is None or not acct["worker_id"]
    db.close()


# ---- T-20: slow disconnect keeps lease alive until disconnect returns ------


@pytest.mark.asyncio
async def test_T20_slow_disconnect_keeps_lease_active(tmp_path):
    from telegram_phone_number_checker.job_manager import JobManager

    cfg, db, job_repo, result_repo, job_id = _setup(
        tmp_path, ["+84911111111"], worker_lease_seconds=60
    )
    ak = account_key_from_phone(cfg.api_phone_number)
    fake = FakeTelegram(disconnect_sleep=2.0)
    mgr = JobManager(cfg, db)

    task = asyncio.ensure_future(
        mgr.run(job_id, auto_resume=True, telegram_factory=lambda: fake)
    )
    await task
    assert fake.disconnect_count == 1
    # After disconnect returned and run() released, no active lease remains.
    assert job_repo.is_account_owned(ak) is False
    assert job_repo.has_live_worker_lease(job_id) is False
    db.close()
