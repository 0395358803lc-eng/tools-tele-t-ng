import asyncio

import pytest

from telegram_phone_number_checker.config import Config
from telegram_phone_number_checker.database import Database
from telegram_phone_number_checker.models import JobStatus
from telegram_phone_number_checker.repositories.job_repository import JobRepository
from telegram_phone_number_checker.webapi.runner import JobRunner
from telegram_phone_number_checker.webapi.sse import SSEHub


@pytest.mark.asyncio
async def test_parallel_parent_runs_each_branch_with_its_own_account(tmp_path, monkeypatch):
    db = Database(tmp_path / "parallel.db")
    cfg = Config()
    cfg.database_url = None
    repo = JobRepository(db)
    parent = repo.create("parent", "multi", job_mode="MULTI_PARENT")
    repo.create("a", "A", 1, "+84911111111", "MULTI_BRANCH", parent.id)
    repo.create("b", "B", 1, "+84922222222", "MULTI_BRANCH", parent.id)

    active = 0
    max_active = 0
    phones = {}
    class FakeManager:
        def __init__(self, branch_cfg, branch_db):
            self.cfg = branch_cfg
            self.db = branch_db

        async def run(self, job_id, auto_resume=False):
            nonlocal active, max_active
            phones[job_id] = self.cfg.api_phone_number
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            repo.mark_finished(job_id)
            active -= 1
            return JobStatus.COMPLETED

        async def recover(self, job_id):
            return None

    import telegram_phone_number_checker.webapi.runner as runner_module
    monkeypatch.setattr(runner_module, "JobManager", FakeManager)
    runner = JobRunner(cfg, db, SSEHub())
    runner.start(parent.id)
    parent_task = runner._tasks[parent.id]
    await parent_task

    assert max_active == 2
    assert phones == {"a": "+84911111111", "b": "+84922222222"}
    assert repo.get("a").status == JobStatus.COMPLETED
    assert repo.get("b").status == JobStatus.COMPLETED
    assert repo.get(parent.id).status == JobStatus.COMPLETED
    db.close()


@pytest.mark.asyncio
async def test_parent_pause_and_cancel_propagate_to_all_branches(tmp_path, monkeypatch):
    db = Database(tmp_path / "parent-actions.db")
    cfg = Config(); cfg.database_url = None
    repo = JobRepository(db)
    parent = repo.create("p", "multi", job_mode="MULTI_PARENT")
    repo.create("a", "A", 1, "+84911111111", "MULTI_BRANCH", parent.id)
    repo.create("b", "B", 1, "+84922222222", "MULTI_BRANCH", parent.id)
    runner = JobRunner(cfg, db, SSEHub())

    runner.pause(parent.id)
    assert [c.status for c in repo.list_children(parent.id)] == [JobStatus.PAUSED, JobStatus.PAUSED]

    started = []
    monkeypatch.setattr(runner, "start", lambda job_id, auto_resume=None: started.append((job_id, auto_resume)))
    runner.resume(parent.id)
    assert started == [(parent.id, True)]

    await runner.cancel(parent.id)
    assert all(c.status == JobStatus.CANCELLED for c in repo.list_children(parent.id))
    assert repo.get_requested_command(parent.id) == "CANCEL"
    db.close()
