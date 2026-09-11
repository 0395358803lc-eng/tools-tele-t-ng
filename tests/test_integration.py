"""Integration test with a fake Telegram backend.

Validates the full pipeline: import -> worker -> checkpoint -> crash recovery
-> resume without rechecking completed records and correctly retrying others.
"""

import asyncio

from telegram_phone_number_checker.checkpoint import CheckpointManager
from telegram_phone_number_checker.database import Database
from telegram_phone_number_checker.models import CheckResponse, CheckStatus, ErrorType
from telegram_phone_number_checker.rate_limiter import RateLimitManager
from telegram_phone_number_checker.repositories.job_repository import JobRepository
from telegram_phone_number_checker.repositories.result_repository import (
    ResultRepository,
)
from telegram_phone_number_checker.retry_queue import RetryQueue
from telegram_phone_number_checker.worker import Worker


def _make_phone(i):
    # Valid Vietnamese mobile numbers
    return f"+849{10000000 + i}"


class FakeTelegramBackend:
    """Deterministic fake backend.

    - Every 10th phone (i % 10 == 0) -> FOUND
    - Every 25th phone (i % 25 == 0) -> RETRY_REQUIRED (retry_contacts)
    - Every 50th phone (i % 50 == 0) -> TEMPORARY_ERROR (network)
    - Otherwise -> NOT_DISCOVERABLE
    """

    def __init__(self, delay=0):
        self.calls = []
        self.delay = delay
        self.in_flight = False

    async def check_phone(self, phone, client_id=0):
        self.calls.append(phone)
        self.in_flight = True
        if self.delay:
            await asyncio.sleep(self.delay)
        i = int(phone[-8:]) - 10000000
        if i % 50 == 0:
            return CheckResponse(
                status=CheckStatus.TEMPORARY_ERROR,
                phone=phone,
                error_type=ErrorType.NETWORK_TIMEOUT.value,
                error_message="simulated timeout",
            )
        if i % 25 == 0:
            return CheckResponse(
                status=CheckStatus.RETRY_REQUIRED,
                phone=phone,
                error_message="retry contacts",
            )
        if i % 10 == 0:
            return CheckResponse(
                status=CheckStatus.FOUND,
                phone=phone,
                telegram_user_id=i,
                username=f"user_{i}",
                first_name="F",
            )
        return CheckResponse(
            status=CheckStatus.NOT_DISCOVERABLE,
            phone=phone,
        )


def _create_job(db, n=100, max_attempts=5):
    job_repo = JobRepository(db)
    repo = ResultRepository(db)
    run_id = f"integ_{n}"
    job_repo.create(run_id, name="integ", total_items=n)
    for i in range(n):
        repo.insert(run_id, _make_phone(i), _make_phone(i), max_attempts)
    return run_id


def _run_worker_until(db, run_id, backend, max_attempts=5, max_steps=None):
    repo = ResultRepository(db)
    checkpoint = CheckpointManager(repo)
    retry = RetryQueue(repo, max_attempts=max_attempts, base_delay=0, max_delay=0)
    rl = RateLimitManager()
    w = Worker(backend, repo, checkpoint, retry, rl)
    w.claim(run_id)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    done = asyncio.Event()

    async def runner():
        task = asyncio.ensure_future(w.process(run_id, done.set))
        if max_steps is not None:
            calls_at_trigger = max(1, max_steps)
            while len(backend.calls) < calls_at_trigger and not done.is_set():
                await asyncio.sleep(0.001)
            if not done.is_set():
                # Simulate an abrupt crash: cancel the worker task while its
                # current request is still in-flight, leaving that item stranded
                # in PROCESSING (exactly what a power-loss/OS-kill between
                # mark_processing and result save looks like at the DB level).
                task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    loop.run_until_complete(runner())
    loop.close()
    return w


def test_full_pipeline_with_crash_and_recovery(tmp_path):
    db = Database(tmp_path / "integ.db")
    run_id = _create_job(db, n=100)

    # First run: process ~37 calls then "crash" (abort), leaving the in-flight
    # item stranded in PROCESSING and some items finished/retry-scheduled.
    # Small per-request delay so the abort lands while a request is mid-flight.
    backend1 = FakeTelegramBackend(delay=0.05)
    _run_worker_until(db, run_id, backend1, max_steps=37)
    calls_phase1 = list(backend1.calls)

    # Capture which items were ALREADY terminal at the end of phase 1 (before
    # crash recovery). These MUST never be re-checked after restart.
    phase1_terminal = set()
    phase1_processing = set()
    pre_repo = ResultRepository(db)
    for i in range(1, 101):
        it = pre_repo.get(i)
        if it.status.value in ("FOUND", "NOT_DISCOVERABLE", "PERMANENT_ERROR"):
            phase1_terminal.add(it.normalized_phone)
        elif it.status.value == "PROCESSING":
            phase1_processing.add(it.normalized_phone)
    assert phase1_processing, "crash should leave at least one PROCESSING item"

    checkpoint = CheckpointManager(ResultRepository(db))
    recovered = checkpoint.recover_interrupted(run_id, recovery_grace_seconds=0)
    assert recovered >= 1

    # Second run: no crash, should finish all remaining without rechecking completed
    backend2 = FakeTelegramBackend()
    _run_worker_until(db, run_id, backend2, max_attempts=5)

    repo = ResultRepository(db)
    counts = {"FOUND": 0, "NOT_DISCOVERABLE": 0, "PERMANENT_ERROR": 0}
    for i in range(1, 101):
        item = repo.get(i)
        counts[item.status.value] = counts.get(item.status.value, 0) + 1

    # Deterministic backend for i in 0..99:
    #   temporary (i%50==0):    0, 50         -> 2, retried then exhausted
    #   retry     (i%25==0):    25, 75        -> 2, retried then exhausted
    #   found     (i%10==0 excl):10..90 (no 0/50) -> 8
    #   not_discoverable: rest               -> 88
    assert counts["FOUND"] == 8
    assert counts["NOT_DISCOVERABLE"] == 88
    assert counts["PERMANENT_ERROR"] == 4
    assert all(
        repo.get(i).status.value
        not in ("PENDING", "PROCESSING", "RETRY_REQUIRED", "TEMPORARY_ERROR")
        for i in range(1, 101)
    )

    # Invariant: once an item reaches a terminal status it is NEVER checked
    # again. Any item terminal at the end of phase 1 must not appear in phase 2.
    for phone in phase1_terminal:
        assert (
            phone not in backend2.calls
        ), f"Completed item {phone} was rechecked after checkpoint"
    # The interrupted (phase-1 PROCESSING) item must have been re-picked in
    # phase 2 (checkpoint recovery), so it should appear in phase 2 calls.
    for phone in phase1_processing:
        assert (
            phone in backend2.calls
        ), f"Interrupted item {phone} was not recovered/retried after restart"

    # No record is lost across the crash: total remains at 100 and no item is
    # ever left PENDING/PROCESSING.
    assert sum(counts.values()) == 100
    db.close()


def test_crash_leaves_no_stuck_processing(tmp_path):
    db = Database(tmp_path / "stuck.db")
    run_id = _create_job(db, n=5, max_attempts=5)
    repo = ResultRepository(db)
    # simulate item left in PROCESSING after power loss
    repo.mark_processing(1)
    checkpoint = CheckpointManager(repo)
    n = checkpoint.recover_interrupted(run_id, recovery_grace_seconds=0)
    assert n == 1
    assert repo.get(1).status.value == "IN_FLIGHT_UNKNOWN"
    assert repo.get(1).recovery_after is not None
    assert repo.get(1).last_error_type == "WORKER_INTERRUPTED"
    db.close()
