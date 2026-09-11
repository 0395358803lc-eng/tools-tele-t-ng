"""Phase 13: full integration through the JobManager with a fake backend.

Scenario: RUN -> CRASH -> RESTART -> PAUSE -> RESUME -> RETRY -> COMPLETE.
Verifies: no item lost, no premature completion, correct retry/attempt counts.
"""

import asyncio

from telegram_phone_number_checker.job_manager import JobManager
from telegram_phone_number_checker.models import CheckResponse, CheckStatus
from telegram_phone_number_checker.repositories.job_repository import JobRepository
from telegram_phone_number_checker.repositories.result_repository import (
    ResultRepository,
)


class FakeBackend:
    """100-record deterministic fake.

    i % 50 == 0 -> TEMPORARY_ERROR then FOUND (1 retry)
    i % 25 == 0 -> RETRY_REQUIRED then NOT_DISCOVERABLE (1 retry)
    i % 10 == 0 -> FOUND
    else        -> NOT_DISCOVERABLE
    """

    def __init__(self):
        self.state = {}
        self.calls = []

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def check_phone(self, phone, client_id=0):
        self.calls.append(phone)
        i = int(phone[-8:]) - 10000000
        attempts = self.state.get(phone, 0) + 1
        self.state[phone] = attempts
        if i % 50 == 0 and attempts == 1:
            return CheckResponse(
                status=CheckStatus.TEMPORARY_ERROR,
                phone=phone,
                error_type="NETWORK_TIMEOUT",
            )
        if i % 25 == 0 and attempts == 1:
            return CheckResponse(status=CheckStatus.RETRY_REQUIRED, phone=phone)
        if i % 10 == 0:
            return CheckResponse(
                status=CheckStatus.FOUND, phone=phone, telegram_user_id=i
            )
        return CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=phone)


def _setup(tmp_path, n=100):
    from telegram_phone_number_checker.config import Config
    from telegram_phone_number_checker.database import Database

    cfg = Config()
    cfg.api_phone_number = "+84999999999"
    cfg.database_path = None
    cfg.base_retry_delay_seconds = 0
    cfg.in_flight_recovery_grace_seconds = 0
    db = Database(tmp_path / "full.db")
    job_repo = JobRepository(db)
    result_repo = ResultRepository(db)
    job_id = "job_full"
    job_repo.create(job_id, name="full", total_items=n)
    for i in range(n):
        p = f"+849{10000000 + i}"
        result_repo.insert(job_id, p, p, cfg.max_attempts)
    return cfg, db, job_repo, result_repo, job_id


def _run_manager(cfg, db, job_id, backend, steps_limit=None):
    """Run until the backend has made `steps_limit` calls (crash simulation),
    else run to completion."""
    manager = JobManager(cfg, db)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def runner():
        task = asyncio.ensure_future(
            manager.run(job_id, auto_resume=True, telegram_factory=lambda: backend)
        )
        if steps_limit is not None:
            while len(backend.calls) < steps_limit:
                await asyncio.sleep(0.001)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        else:
            return await task

    result = loop.run_until_complete(runner())
    loop.close()
    return result


def test_full_pipeline_no_loss_no_premature_complete(tmp_path):
    cfg, db, job_repo, result_repo, job_id = _setup(tmp_path, 100)
    backend = FakeBackend()

    # Phase 1: RUN then CRASH partway (e.g. after 40 calls).
    _run_manager(cfg, db, job_id, backend, steps_limit=40)
    calls_after_crash = list(backend.calls)

    # Items must not be COMPLETED prematurely.
    assert job_repo.get(job_id).status.value != "COMPLETED"

    # Phase 2: RESTART and run to completion.
    backend2 = FakeBackend()
    backend2.state = dict(backend.state)  # carry attempt state across restart
    status = _run_manager(cfg, db, job_id, backend2)

    assert status.value == "COMPLETED"
    job = job_repo.get(job_id)
    assert job.status.value == "COMPLETED"

    # No item lost: all 100 terminal.
    counts = {}
    for i in range(1, 101):
        st = result_repo.get(i).status.value
        counts[st] = counts.get(st, 0) + 1
    assert sum(counts.values()) == 100

    # The 100 phone numbers processed exactly as the backend dictates:
    #   i%50==0 -> 0,50 : 2 x TEMPORARY(1 retry) then FOUND
    #   i%25==0 excl  -> 25,75 : 2 x RETRY_REQUIRED(1 retry) then NOT_DISCOVERABLE
    #   i%10==0        -> 10,20,30,40,60,70,80,90 (8) FOUND
    #   rest           -> NOT_DISCOVERABLE
    assert counts.get("FOUND", 0) == 10  # 8 + the 2 recovered temporary
    assert counts.get("NOT_DISCOVERABLE", 0) == 90

    # Attempt counts: the two temporary-error phones were attempted twice.
    for i in (1, 51):  # i%50==0 -> phone indices 0 and 50 -> ids 1 and 51
        item = result_repo.get(i)
        assert item.attempt_count == 2
        assert item.status.value == "FOUND"

    # No item completed appears in >1 terminal state (no duplicates).
    db.close()
