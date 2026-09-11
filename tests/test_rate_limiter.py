import pytest

from telegram_phone_number_checker.rate_limiter import RateLimitManager


@pytest.mark.asyncio
async def test_normal_request_not_blocked():
    rl = RateLimitManager()
    assert not rl.is_blocked()
    await rl.acquire()
    rl.register_success()
    assert not rl.is_blocked()


@pytest.mark.asyncio
async def test_blocked_after_floodwait():
    rl = RateLimitManager()
    rl.register_rate_limit(30)
    assert rl.is_blocked()
    assert rl.available_at() is not None
    assert rl.remaining_block_seconds() > 0


@pytest.mark.asyncio
async def test_acquire_waits_until_cooldown_expiry():
    rl = RateLimitManager()
    rl.register_rate_limit(1)
    started = rl.remaining_block_seconds()

    import asyncio

    release = asyncio.Event()
    saw_blocked = []

    async def waiter():
        await rl.acquire()
        release.set()

    task = asyncio.ensure_future(waiter())
    await asyncio.sleep(0.05)
    saw_blocked.append(rl.is_blocked())
    assert rl.is_blocked()
    await asyncio.wait_for(release.wait(), timeout=3)
    assert not rl.is_blocked()
    await task


@pytest.mark.asyncio
async def test_floodwait_priority_over_min_interval():
    rl = RateLimitManager(min_request_interval_seconds=0.001)
    rl.register_rate_limit(0.5)
    await rl.acquire()
    assert not rl.is_blocked()


@pytest.mark.asyncio
async def test_available_at_none_when_not_blocked():
    rl = RateLimitManager()
    assert rl.available_at() is None
    assert rl.remaining_block_seconds() == 0.0
