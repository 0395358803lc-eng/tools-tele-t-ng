"""Phase 18: database invariant validation tests."""

from telegram_phone_number_checker.database import Database
from telegram_phone_number_checker.database_validation import validate_database_state
from telegram_phone_number_checker.models import CheckStatus, JobStatus
from telegram_phone_number_checker.repositories.job_repository import JobRepository
from telegram_phone_number_checker.repositories.result_repository import (
    ResultRepository,
)


def _setup(temp_dir) -> tuple:
    db = Database(temp_dir / "val.db")
    job_repo = JobRepository(db)
    result_repo = ResultRepository(db)
    job_id = "job_val"
    job_repo.create(job_id, name="val", total_items=1)
    return db, job_repo, result_repo, job_id


def test_consistent_database_ok(tmp_path):
    db, job_repo, result_repo, job_id = _setup(tmp_path)
    result_repo.insert(job_id, "+84911111111", "+84911111111", 5)
    result_repo.save_result(1, CheckStatus.NOT_DISCOVERABLE, completed=True)
    job_repo.mark_finished(job_id)
    assert validate_database_state(db) == []
    db.close()


def test_completed_with_unfinished_item_is_error(tmp_path):
    db, job_repo, result_repo, job_id = _setup(tmp_path)
    result_repo.insert(job_id, "+84911111111", "+84911111111", 5)
    # Job COMPLETED but item still PENDING -> invariant violation
    job_repo.mark_finished(job_id)
    findings = validate_database_state(db)
    assert any("unfinished" in m.lower() for _, m in findings)
    db.close()


def test_completed_with_worker_id_is_error(tmp_path):
    db, job_repo, result_repo, job_id = _setup(tmp_path)
    result_repo.insert(job_id, "+84911111111", "+84911111111", 5)
    result_repo.save_result(1, CheckStatus.NOT_DISCOVERABLE, completed=True)
    job_repo.mark_finished(job_id)
    # Reintroduce a stale worker_id on a COMPLETED job
    job_repo.claim_worker(job_id, "ghostworker00000000", lease_seconds=60)
    findings = validate_database_state(db)
    assert any("worker_id is not NULL" in m for _, m in findings)
    db.close()


def test_terminal_item_without_completed_at_is_error(tmp_path):
    db, job_repo, result_repo, job_id = _setup(tmp_path)
    result_repo.insert(job_id, "+84911111111", "+84911111111", 5)
    # Save as FOUND but with completed=False -> terminal yet no completed_at
    result_repo.save_result(1, CheckStatus.FOUND)
    findings = validate_database_state(db)
    assert any("completed_at is NULL" in m for _, m in findings)
    db.close()


def test_paused_with_active_lease_is_error(tmp_path):
    """P1-04: PAUSED job must not hold a live worker lease."""
    db, job_repo, result_repo, job_id = _setup(tmp_path)
    job_repo.claim_worker(job_id, "workerpause000000", lease_seconds=60)
    job_repo.update_status(job_id, JobStatus.PAUSED)
    findings = validate_database_state(db)
    assert any("PAUSED but worker lease still active" in m for _, m in findings)
    db.close()


def test_duplicate_worker_owns_two_jobs_is_error(tmp_path):
    """P1-04: one worker_id owning more than one active job is a violation."""
    db, job_repo, result_repo, job_id = _setup(tmp_path)
    job2 = "job_dup"
    job_repo.create(job2, total_items=1)
    job_repo.claim_worker(job_id, "dupeworker000000", lease_seconds=60)
    job_repo.claim_worker(job2, "dupeworker000000", lease_seconds=60)
    job_repo.update_status(job_id, JobStatus.RUNNING)
    job_repo.update_status(job2, JobStatus.RUNNING)
    findings = validate_database_state(db)
    assert any("duplicate worker" in m.lower() for _, m in findings)
    db.close()


def test_processing_without_token_is_error(tmp_path):
    db, _, result_repo, job_id = _setup(tmp_path)
    item = result_repo.insert(job_id, "+84911111111", "+84911111111", 5)
    db.execute(
        "UPDATE check_items SET status = 'PROCESSING', processing_token = NULL "
        "WHERE id = ?",
        (item.id,),
    )
    db.commit()
    findings = validate_database_state(db)
    assert any("PROCESSING but processing_token is NULL" in m for _, m in findings)
    db.close()


def test_in_flight_unknown_without_recovery_after_is_error(tmp_path):
    db, _, result_repo, job_id = _setup(tmp_path)
    item = result_repo.insert(job_id, "+84911111111", "+84911111111", 5)
    db.execute(
        "UPDATE check_items SET status = 'IN_FLIGHT_UNKNOWN', "
        "recovery_after = NULL WHERE id = ?",
        (item.id,),
    )
    db.commit()
    findings = validate_database_state(db)
    assert any("IN_FLIGHT_UNKNOWN but recovery_after is NULL" in m for _, m in findings)
    db.close()


def test_completed_with_active_account_lease_is_error(tmp_path):
    db, job_repo, result_repo, job_id = _setup(tmp_path)
    result_repo.insert(job_id, "+84911111111", "+84911111111", 5)
    result_repo.save_result(1, CheckStatus.NOT_DISCOVERABLE, completed=True)
    job_repo.mark_finished(job_id)
    assert job_repo.claim_account("account", "ghostworker", job_id, 60)
    findings = validate_database_state(db)
    assert any("live lease for terminal job" in m for _, m in findings)
    db.close()
