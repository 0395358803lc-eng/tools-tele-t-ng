import pytest

from telegram_phone_number_checker.config import Config
from telegram_phone_number_checker.database import Database
from telegram_phone_number_checker.main import _create_job_with_numbers
from telegram_phone_number_checker.repositories.job_repository import JobRepository
from telegram_phone_number_checker.repositories.result_repository import ResultRepository


def test_transaction_rolls_back_repository_commit(tmp_path):
    db = Database(tmp_path / "tx.db")
    repo = JobRepository(db)
    with pytest.raises(RuntimeError):
        with db.transaction():
            repo.create("tx-job", "atomic")
            raise RuntimeError("boom")
    assert repo.get("tx-job") is None
    db.close()


def test_create_job_is_atomic_when_item_insert_fails(tmp_path, monkeypatch):
    db = Database(tmp_path / "create-atomic.db")
    cfg = Config()
    cfg.database_url = None
    monkeypatch.setattr(ResultRepository, "insert", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("insert failed")))
    with pytest.raises(RuntimeError, match="insert failed"):
        _create_job_with_numbers(db, cfg, "+84911111111", "atomic-create")
    assert db.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"] == 0
    assert db.execute("SELECT COUNT(*) AS c FROM check_items").fetchone()["c"] == 0
    db.close()


def test_nested_job_transaction_rolls_back_parent_and_child(tmp_path):
    db = Database(tmp_path / "nested.db")
    cfg = Config()
    cfg.database_url = None
    repo = JobRepository(db)
    with pytest.raises(RuntimeError):
        with db.transaction():
            repo.create("parent", "multi", job_mode="MULTI_PARENT")
            _create_job_with_numbers(
                db, cfg, "+84911111111", "branch",
                job_mode="MULTI_BRANCH", parent_job_id="parent",
            )
            raise RuntimeError("abort parent")
    assert repo.get("parent") is None
    assert db.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"] == 0
    db.close()
