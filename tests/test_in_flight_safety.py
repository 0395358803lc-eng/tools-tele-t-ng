"""T21-T30: in-flight fencing, ownership-safe writes, and cleanup safety."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from telegram_phone_number_checker.checkpoint import CheckpointManager
from telegram_phone_number_checker.config import Config
from telegram_phone_number_checker.database import Database
from telegram_phone_number_checker.job_manager import JobManager
from telegram_phone_number_checker.lease_keeper import LostOwnershipError
from telegram_phone_number_checker.models import (
    CheckResponse,
    CheckStatus,
    JobStatus,
    iso_from_offset,
)
from telegram_phone_number_checker.rate_limiter import (
    RateLimitManager,
    account_key_from_phone,
)
from telegram_phone_number_checker.repositories.job_repository import JobRepository
from telegram_phone_number_checker.repositories.result_repository import (
    ResultRepository,
)
from telegram_phone_number_checker.retry_queue import RetryQueue
from telegram_phone_number_checker.worker import Worker


class FakeTelegram:
    def __init__(self, *, slow=False, disconnect_error=None):
        self.slow = slow
        self.disconnect_error = disconnect_error
        self.started = asyncio.Event()
        self.cancelled = False

    async def connect(self):
        return None

    async def disconnect(self):
        if self.disconnect_error:
            raise self.disconnect_error

    async def check_phone(self, phone, client_id=0):
        self.started.set()
        try:
            if self.slow:
                await asyncio.sleep(30)
            return CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=phone)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _env(tmp_path, name="inflight"):
    db = Database(tmp_path / f"{name}.db")
    jobs = JobRepository(db)
    items = ResultRepository(db)
    jobs.create("job", total_items=1)
    item = items.insert("job", "+84911111111", "+84911111111", 3)
    return db, jobs, items, item


def _worker(db, jobs, items, telegram, *, grace=60, lease=30):
    checkpoint = CheckpointManager(items)
    retry = RetryQueue(items, max_attempts=3, base_delay=0, max_delay=0)
    account = account_key_from_phone("+84999999999")
    return Worker(
        telegram,
        items,
        checkpoint,
        retry,
        RateLimitManager(db=db, account_key=account),
        job_repo=jobs,
        account_key=account,
        lease_seconds=lease,
        renew_failure_limit=1,
        in_flight_recovery_grace_seconds=grace,
    )


@pytest.mark.asyncio
async def test_T21_disconnect_error_still_releases_all_leases(tmp_path):
    db, jobs, items, _ = _env(tmp_path, "t21")
    cfg = Config()
    cfg.api_phone_number = "+84999999999"
    cfg.database_path = None
    fake = FakeTelegram(disconnect_error=RuntimeError("disconnect failed"))

    with pytest.raises(RuntimeError, match="disconnect failed"):
        await JobManager(cfg, db).run(
            "job", auto_resume=True, telegram_factory=lambda: fake
        )

    job = jobs.get("job")
    assert job.status == JobStatus.COMPLETED
    assert job.worker_id is None and job.worker_lease_until is None
    assert jobs.is_account_owned(account_key_from_phone(cfg.api_phone_number)) is False
    db.close()


@pytest.mark.asyncio
async def test_T22_heartbeat_loss_cancels_and_quarantines_request(tmp_path):
    db, jobs, items, item = _env(tmp_path, "t22")
    fake = FakeTelegram(slow=True)
    worker = _worker(db, jobs, items, fake, grace=120)
    worker.claim("job")
    jobs.update_status_if_owned("job", worker.worker_id, JobStatus.RUNNING)
    await worker._start_lease_keeper()
    done = asyncio.Event()
    task = asyncio.create_task(worker.process("job", done.set))
    await asyncio.wait_for(fake.started.wait(), timeout=2)

    worker.lease_keeper._mark_lost("TEST_HEARTBEAT_FAILED")
    with pytest.raises(LostOwnershipError):
        await asyncio.wait_for(task, timeout=2)

    quarantined = items.get(item.id)
    assert fake.cancelled is True
    assert quarantined.status == CheckStatus.IN_FLIGHT_UNKNOWN
    assert quarantined.ownership_lost_at is not None
    assert quarantined.recovery_after is not None
    await worker.lease_keeper.stop()
    worker.release()
    db.close()


def test_T23_takeover_respects_in_flight_recovery_after(tmp_path):
    db, _, items, item = _env(tmp_path, "t23")
    token = items.mark_processing(item.id)
    assert items.mark_in_flight_unknown(item.id, token, iso_from_offset(120))
    assert items.next_due_item("job") is None
    assert items.get(item.id).status == CheckStatus.IN_FLIGHT_UNKNOWN
    db.close()


def test_T24_old_worker_cannot_overwrite_new_worker_job_status(tmp_path):
    db, jobs, _, _ = _env(tmp_path, "t24")
    assert jobs.claim_worker("job", "worker-a", 60)
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    db.execute("UPDATE jobs SET worker_lease_until = ? WHERE id = 'job'", (past,))
    db.commit()
    assert jobs.claim_worker("job", "worker-b", 60)
    assert jobs.update_status_if_owned("job", "worker-b", JobStatus.COMPLETED)
    assert not jobs.update_status_if_owned("job", "worker-a", JobStatus.PAUSED)
    assert jobs.get("job").status == JobStatus.COMPLETED
    db.close()


def test_T25_old_worker_cannot_overwrite_new_item_token(tmp_path):
    db, jobs, items, item = _env(tmp_path, "t25")
    assert jobs.claim_worker("job", "worker-a", 60)
    token_a = items.mark_processing(item.id, "job", "worker-a")
    assert token_a
    assert items.mark_in_flight_unknown(item.id, token_a, iso_from_offset(0))
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    db.execute("UPDATE jobs SET worker_lease_until = ? WHERE id = 'job'", (past,))
    db.commit()
    assert jobs.claim_worker("job", "worker-b", 60)
    token_b = items.mark_processing(item.id, "job", "worker-b")
    assert token_b and token_b != token_a

    assert not items.save_result(
        item.id,
        CheckStatus.FOUND,
        completed=True,
        processing_token=token_a,
        job_id="job",
        worker_id="worker-a",
    )
    current = items.get(item.id)
    assert current.status == CheckStatus.PROCESSING
    assert current.processing_token == token_b
    db.close()


@pytest.mark.asyncio
async def test_T26_lost_owner_manager_does_not_mutate_successor_status(tmp_path):
    db, jobs, _, _ = _env(tmp_path, "t26")
    cfg = Config()
    cfg.api_phone_number = "+84999999999"
    cfg.database_path = None
    cfg.worker_lease_seconds = 2
    cfg.lease_renew_failure_limit = 1
    fake = FakeTelegram(slow=True)
    run_task = asyncio.create_task(
        JobManager(cfg, db).run("job", auto_resume=True, telegram_factory=lambda: fake)
    )
    await asyncio.wait_for(fake.started.wait(), timeout=3)

    future = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    db.execute(
        "UPDATE jobs SET worker_id = 'worker-b', worker_lease_until = ?, "
        "worker_heartbeat_at = ?, status = 'COMPLETED' WHERE id = 'job'",
        (future, future),
    )
    db.execute(
        "UPDATE account_worker_state SET worker_id = 'worker-b', "
        "worker_lease_until = ?, worker_heartbeat_at = ? WHERE job_id = 'job'",
        (future, future),
    )
    db.commit()

    with pytest.raises(LostOwnershipError):
        await asyncio.wait_for(run_task, timeout=5)
    successor = jobs.get("job")
    assert successor.status == JobStatus.COMPLETED
    assert successor.worker_id == "worker-b"
    db.close()


class ReleaseProbe:
    def __init__(self, fail_account=False, fail_job=False):
        self.fail_account = fail_account
        self.fail_job = fail_job
        self.calls = []

    def release_account(self, account, worker, job):
        self.calls.append("account")
        if self.fail_account:
            raise RuntimeError("account release failed")

    def release_worker(self, job, worker):
        self.calls.append("job")
        if self.fail_job:
            raise RuntimeError("job release failed")


def _release_worker(probe):
    worker = Worker(None, None, None, None, None, job_repo=probe, account_key="acct")
    worker._job_id = "job"
    worker._claimed_account = True
    return worker


def test_T27_account_release_failure_still_attempts_job_release():
    probe = ReleaseProbe(fail_account=True)
    with pytest.raises(RuntimeError, match="account release failed"):
        _release_worker(probe).release()
    assert probe.calls == ["account", "job"]


def test_T28_job_release_failure_happens_after_account_attempt():
    probe = ReleaseProbe(fail_job=True)
    with pytest.raises(RuntimeError, match="job release failed"):
        _release_worker(probe).release()
    assert probe.calls == ["account", "job"]


def test_T29_processing_token_race_rejects_A_after_B_claim(tmp_path):
    db, _, items, item = _env(tmp_path, "t29")
    token_a = items.mark_processing(item.id)
    assert items.mark_in_flight_unknown(item.id, token_a, iso_from_offset(0))
    token_b = items.mark_processing(item.id)
    assert token_b != token_a
    assert not items.save_result(
        item.id,
        CheckStatus.FOUND,
        completed=True,
        processing_token=token_a,
    )
    assert items.get(item.id).processing_token == token_b
    db.close()


def test_T30_in_flight_recovery_becomes_selectable_only_after_grace(tmp_path):
    db, _, items, item = _env(tmp_path, "t30")
    token = items.mark_processing(item.id)
    assert items.mark_in_flight_unknown(item.id, token, iso_from_offset(120))
    assert items.next_due_item("job") is None

    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    db.execute(
        "UPDATE check_items SET recovery_after = ? WHERE id = ?", (past, item.id)
    )
    db.commit()
    assert items.next_due_item("job").id == item.id
    new_token = items.mark_processing(item.id)
    assert new_token and new_token != token
    db.close()
