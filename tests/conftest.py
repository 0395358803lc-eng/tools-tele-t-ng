import os
import tempfile

import pytest

from telegram_phone_number_checker.config import Config
from telegram_phone_number_checker.database import Database
from telegram_phone_number_checker.repositories.job_repository import JobRepository
from telegram_phone_number_checker.repositories.result_repository import (
    ResultRepository,
)


@pytest.fixture
def temp_db(tmp_path):
    db = Database(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def job_repo(temp_db):
    return JobRepository(temp_db)


@pytest.fixture
def result_repo(temp_db):
    return ResultRepository(temp_db)


@pytest.fixture
def config():
    cfg = Config()
    cfg.database_path = None
    return cfg


@pytest.fixture
def job_id(temp_db, job_repo):
    job = job_repo.create("job_test", name="test", total_items=0)
    return job.id
