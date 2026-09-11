import sqlite3

import pytest

from telegram_phone_number_checker.database import Database
from telegram_phone_number_checker.models import CheckStatus, JobStatus
from telegram_phone_number_checker.repositories.job_repository import JobRepository
from telegram_phone_number_checker.repositories.result_repository import (
    ResultRepository,
)


def test_schema_created(temp_db):
    tables = temp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {t["name"] for t in tables}
    assert "jobs" in names
    assert "check_items" in names


def test_indexes_created(temp_db):
    indexes = temp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    names = {i["name"] for i in indexes}
    assert "idx_check_items_job_status" in names
    assert "idx_check_items_retry" in names


def test_migration_adds_in_flight_fencing_columns(tmp_path):
    path = tmp_path / "pre_132.db"
    original = Database(path)
    original.close()

    # Model a pre-1.3.2 database by removing only the newly introduced fields.
    conn = sqlite3.connect(path)
    for column in (
        "processing_token",
        "recovery_after",
        "ownership_lost_at",
        "in_flight_started_at",
    ):
        conn.execute(f"ALTER TABLE check_items DROP COLUMN {column}")
    conn.commit()
    conn.close()

    migrated = Database(path)
    columns = {
        row["name"]
        for row in migrated.execute("PRAGMA table_info(check_items)").fetchall()
    }
    assert {
        "processing_token",
        "recovery_after",
        "ownership_lost_at",
        "in_flight_started_at",
    } <= columns
    migrated.close()


def test_job_crud(temp_db, job_repo):
    job = job_repo.create("j1", name="mine", total_items=100)
    assert job_repo.get("j1").status == JobStatus.CREATED
    job_repo.mark_started("j1")
    assert job_repo.get("j1").status == JobStatus.RUNNING
    job_repo.mark_finished("j1")
    assert job_repo.get("j1").status == JobStatus.COMPLETED
    assert len(job_repo.list()) == 1


def test_item_normalization_dedup(temp_db, job_id, result_repo):
    a = result_repo.insert(job_id, "+84 911", "+84911000000", 5)
    exists = result_repo.exists(job_id, "+84911000000")
    assert exists is True
    assert result_repo.get(a.id).normalized_phone == "+84911000000"


def test_reconcile_stats(temp_db, job_id, job_repo, result_repo):
    result_repo.insert(job_id, "+84911111111", "+84911111111", 5)
    result_repo.insert(job_id, "+84922222222", "+84922222222", 5)
    result_repo.insert(job_id, "+84933333333", "+84933333333", 5)
    result_repo.save_result(1, CheckStatus.FOUND, completed=True)
    result_repo.save_result(2, CheckStatus.NOT_DISCOVERABLE, completed=True)
    result_repo.save_result(3, CheckStatus.PERMANENT_ERROR, completed=True)
    job_repo.reconcile_stats(job_id)
    job = job_repo.get(job_id)
    assert job.total_items == 3
    assert job.processed_items == 3
    assert job.found_items == 1
    assert job.not_discoverable_items == 1
    assert job.failed_items == 1


def test_foreign_key_enforced(temp_db):
    with pytest.raises(Exception):
        temp_db.execute(
            "INSERT INTO check_items (job_id, status, created_at, updated_at) VALUES ('nope', 'PENDING', 'x', 'x')"
        )
        temp_db.commit()


def test_insert_invalid_is_permanent_error(temp_db, job_id, result_repo):
    item = result_repo.insert_invalid(job_id, "not-a-phone")
    stored = result_repo.get(item.id)
    assert stored.status == CheckStatus.PERMANENT_ERROR
    assert stored.normalized_phone is None
    assert stored.last_error_type == "INVALID_PHONE"
    assert stored.completed_at is not None
    # A terminal item is not unfinished and must not be picked for checking.
    assert result_repo.has_unfinished_items(job_id) is False
    assert result_repo.next_due_item(job_id) is None


def test_has_unfinished_items_and_next_retry_at(temp_db, job_id, result_repo):
    from telegram_phone_number_checker.models import iso_from_offset

    result_repo.insert(job_id, "+84911111111", "+84911111111", 5)
    result_repo.insert(job_id, "+84922222222", "+84922222222", 5)
    assert result_repo.has_unfinished_items(job_id) is True

    result_repo.save_result(1, CheckStatus.FOUND, completed=True)
    # Item 2 is still PENDING -> unfinished, but no future retry exists yet.
    assert result_repo.has_unfinished_items(job_id) is True
    assert result_repo.get_next_retry_at(job_id) is None

    # Schedule a future retry on item 2.
    result_repo.save_result(
        2,
        CheckStatus.RETRY_REQUIRED,
        attempt_count=1,
        next_retry_at=iso_from_offset(60),
    )
    assert result_repo.has_unfinished_items(job_id) is True
    assert result_repo.get_next_retry_at(job_id) is not None

    # Once terminal, nothing is unfinished.
    result_repo.save_result(2, CheckStatus.NOT_DISCOVERABLE, completed=True)
    assert result_repo.has_unfinished_items(job_id) is False


def test_executemany_uses_cursor_for_postgres_adapter():
    import threading

    class Cursor:
        def __init__(self): self.rows = None; self.closed = False
        def executemany(self, statement, values): self.rows = (statement, values)
        def close(self): self.closed = True

    class Connection:
        def __init__(self): self.cursor_obj = Cursor(); self.commits = 0
        def cursor(self): return self.cursor_obj
        def commit(self): self.commits += 1

    db = Database.__new__(Database)
    db._lock = threading.RLock(); db._use_postgres = True; db._transaction_depth = 0
    db._conn = Connection(); db._adapt_sql = lambda sql: sql.replace("?", "%s")
    db._ensure_postgres_connection = lambda: None
    db._connection_lost = lambda exc: False
    db._reconnect_postgres = lambda: None
    db.executemany("INSERT INTO t(a) VALUES(?)", [(1,), (2,)])
    assert db._conn.cursor_obj.rows == ("INSERT INTO t(a) VALUES(%s)", [(1,), (2,)])
    assert db._conn.cursor_obj.closed is True
    assert db._conn.commits == 1
