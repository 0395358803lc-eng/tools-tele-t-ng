from telegram_phone_number_checker.models import CheckStatus
from telegram_phone_number_checker.repositories.result_repository import (
    ResultRepository,
)
from telegram_phone_number_checker.retry_queue import (
    RetryQueue,
    compute_backoff,
    iso_future,
)


def _item(repo, job_id, phone="+84911111111", norm="+84911111111"):
    return repo.insert(job_id, phone, norm, max_attempts=2)


def test_pending_selected(temp_db, job_id, result_repo):
    item = _item(result_repo, job_id)
    q = RetryQueue(result_repo, max_attempts=5, base_delay=30, max_delay=3600)
    assert q.next(job_id).id == item.id


def test_future_retry_ignored(temp_db, job_id, result_repo):
    from datetime import datetime, timedelta, timezone

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    item = _item(result_repo, job_id)
    result_repo.mark_retry(item.id, 1, future)
    q = RetryQueue(result_repo, max_attempts=5, base_delay=30, max_delay=3600)
    assert q.next(job_id) is None


def test_expired_retry_selected(temp_db, job_id, result_repo):
    from datetime import datetime, timedelta, timezone

    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    item = _item(result_repo, job_id)
    result_repo.mark_retry(item.id, 1, past)
    q = RetryQueue(result_repo, max_attempts=5, base_delay=30, max_delay=3600)
    assert q.next(job_id).id == item.id


def test_max_attempts_respected(temp_db, job_id, result_repo):
    item = _item(result_repo, job_id)
    q = RetryQueue(result_repo, max_attempts=2, base_delay=30, max_delay=3600)
    q.schedule_retry(item, error_type="X")
    after1 = result_repo.get(item.id)
    assert after1.status == CheckStatus.RETRY_REQUIRED
    assert after1.attempt_count == 1
    q.schedule_retry(after1, error_type="X")
    after2 = result_repo.get(item.id)
    assert after2.attempt_count == 2
    q.schedule_retry(after2, error_type="X")
    final = result_repo.get(item.id)
    assert final.status == CheckStatus.PERMANENT_ERROR
    assert final.last_error_type == "RETRY_EXHAUSTED"


def test_max_attempts_respected_partial(temp_db, job_id, result_repo):
    # The item's OWN max_attempts governs (Phase 10), not the queue config.
    # An item created with max_attempts=1 exhausts on its first failed attempt.
    item = result_repo.insert(job_id, "+84922222222", "+84922222222", max_attempts=1)
    # Even a queue configured for more attempts cannot exceed item.max_attempts.
    q = RetryQueue(result_repo, max_attempts=5, base_delay=30, max_delay=3600)
    q.schedule_retry(item, error_type="X")
    final = result_repo.get(item.id)
    assert final.status == CheckStatus.PERMANENT_ERROR
    assert final.last_error_type == "RETRY_EXHAUSTED"


def test_item_max_attempts_survives_config_change(temp_db, job_id, result_repo):
    # Item created when MAX_ATTEMPTS=5 must keep 5 even if the running config
    # later drops to a smaller number: item.max_attempts is the source of truth.
    item = result_repo.insert(job_id, "+84933333333", "+84933333333", max_attempts=5)
    q = RetryQueue(result_repo, max_attempts=2, base_delay=30, max_delay=3600)
    for _ in range(4):
        cur = result_repo.get(item.id)
        q.schedule_retry(cur, error_type="X")
    final = result_repo.get(item.id)
    # 4 retries succeeded (item keeps 5) -> still RETRY_REQUIRED, not exhausted
    assert final.status == CheckStatus.RETRY_REQUIRED
    assert final.attempt_count == 4
    # A 5th retry exhausts it.
    q.schedule_retry(final, error_type="X")
    exhausted = result_repo.get(item.id)
    assert exhausted.status == CheckStatus.PERMANENT_ERROR


def test_compute_backoff_grows():
    assert compute_backoff(1, 30, 3600) <= 30
    assert compute_backoff(3, 30, 3600) <= 120
    assert compute_backoff(20, 30, 3600) <= 3600


def test_iso_future_is_future():
    from datetime import datetime, timezone

    fut = iso_future(60)
    parsed = datetime.fromisoformat(fut.replace("Z", "+00:00"))
    assert parsed > datetime.now(timezone.utc)
