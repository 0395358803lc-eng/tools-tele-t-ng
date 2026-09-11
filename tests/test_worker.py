import asyncio

import pytest

from telegram_phone_number_checker.checkpoint import CheckpointManager
from telegram_phone_number_checker.models import CheckResponse, CheckStatus, JobCommand
from telegram_phone_number_checker.rate_limiter import RateLimitManager
from telegram_phone_number_checker.repositories.result_repository import (
    ResultRepository,
)
from telegram_phone_number_checker.retry_queue import RetryQueue
from telegram_phone_number_checker.worker import Worker


class FakeTelegram:
    def __init__(self, responses=None, rate_limit_at_status=None, rate_limit_seconds=0):
        self.responses = responses or []
        self.calls = []

    async def check_phone(self, phone, client_id=0):
        self.calls.append(phone)
        if self.responses:
            return self.responses.pop(0)
        return CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone=phone)


def _worker(temp_db, job_id, result_repo, telegram):
    checkpoint = CheckpointManager(result_repo)
    retry = RetryQueue(result_repo, max_attempts=2, base_delay=0, max_delay=0)
    rl = RateLimitManager()
    return Worker(telegram, result_repo, checkpoint, retry, rl)


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_worker_found_saves_data(temp_db, job_id, result_repo):
    result_repo.insert(job_id, "+84911111111", "+84911111111", 5)
    telegram = FakeTelegram(
        responses=[
            CheckResponse(
                status=CheckStatus.FOUND,
                phone="+84911111111",
                telegram_user_id=99,
                username="bob",
                first_name="Bob",
            )
        ]
    )
    w = _worker(temp_db, job_id, result_repo, telegram)
    done = asyncio.Event()
    w.claim(job_id)
    _run(w.process(job_id, done.set))
    item = result_repo.get(1)
    assert item.status == CheckStatus.FOUND
    assert item.telegram_user_id == 99
    assert item.completed_at is not None


def test_worker_not_discoverable(temp_db, job_id, result_repo):
    result_repo.insert(job_id, "+84911111111", "+84911111111", 5)
    telegram = FakeTelegram(
        responses=[
            CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone="+84911111111")
        ]
    )
    w = _worker(temp_db, job_id, result_repo, telegram)
    done = asyncio.Event()
    w.claim(job_id)
    _run(w.process(job_id, done.set))
    assert result_repo.get(1).status == CheckStatus.NOT_DISCOVERABLE


def test_worker_floodwait_marks_rate_limited_and_retries(temp_db, job_id, result_repo):
    result_repo.insert(job_id, "+84911111111", "+84911111111", 5)
    telegram = FakeTelegram(
        responses=[
            CheckResponse(
                status=CheckStatus.RATE_LIMITED,
                phone="+84911111111",
                retry_after_seconds=1,
            ),
            CheckResponse(
                status=CheckStatus.FOUND, phone="+84911111111", telegram_user_id=7
            ),
        ]
    )
    w = _worker(temp_db, job_id, result_repo, telegram)
    assert w.paused_on_rate_limit is False
    done = asyncio.Event()
    w.claim(job_id)
    _run(w.process(job_id, done.set))
    assert result_repo.get(1).status == CheckStatus.FOUND
    assert len(telegram.calls) >= 2


def test_worker_with_pause_command_stops(temp_db, job_id, result_repo):
    result_repo.insert(job_id, "+84911111111", "+84911111111", 5)
    result_repo.insert(job_id, "+84922222222", "+84922222222", 5)
    telegram = FakeTelegram(
        responses=[
            CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone="+84911111111"),
            CheckResponse(status=CheckStatus.NOT_DISCOVERABLE, phone="+84922222222"),
        ]
    )
    w = _worker(temp_db, job_id, result_repo, telegram)

    loop = asyncio.new_event_loop()
    w.claim(job_id)
    task = loop.create_task(w.process(job_id, lambda: None))
    w.set_command(JobCommand.PAUSE)
    loop.run_until_complete(task)
    loop.close()
    # Pause must stop the loop before exhausting all items.
    assert len(telegram.calls) < 2
