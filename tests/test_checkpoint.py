from telegram_phone_number_checker.checkpoint import CheckpointManager
from telegram_phone_number_checker.models import CheckStatus


def test_crash_after_processing_recovers_item(temp_db, job_id, result_repo):
    item = result_repo.insert(job_id, "+84911111111", "+84911111111", max_attempts=5)
    result_repo.mark_processing(item.id)

    checkpoint = CheckpointManager(result_repo)
    recovered = checkpoint.recover_interrupted(job_id, recovery_grace_seconds=0)

    assert recovered == 1
    recovered_item = result_repo.get(item.id)
    assert recovered_item.status == CheckStatus.IN_FLIGHT_UNKNOWN
    assert recovered_item.recovery_after is not None
    assert recovered_item.last_error_type == "WORKER_INTERRUPTED"
    # Grace has elapsed, so it is now eligible for controlled re-pick.
    assert checkpoint.next_item(job_id).id == item.id


def test_completed_items_not_repicked(temp_db, job_id, result_repo):
    result_repo.insert(job_id, "+84911111111", "+84911111111", max_attempts=5)
    result_repo.insert(job_id, "+84922222222", "+84922222222", max_attempts=5)
    result_repo.insert(job_id, "+84933333333", "+84933333333", max_attempts=5)

    items = []
    for i in range(1, 4):
        items.append(result_repo.get(i))

    result_repo.save_result(1, CheckStatus.FOUND, completed=True)
    result_repo.save_result(2, CheckStatus.NOT_DISCOVERABLE, completed=True)
    result_repo.save_result(3, CheckStatus.PERMANENT_ERROR, completed=True)

    checkpoint = CheckpointManager(result_repo)
    assert checkpoint.next_item(job_id) is None
    assert checkpoint.count_pending(job_id) == 0
