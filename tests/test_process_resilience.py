import asyncio
import os
import subprocess
import sys
from pathlib import Path

from telegram_phone_number_checker.config import Config
from telegram_phone_number_checker.database import Database
from telegram_phone_number_checker.job_manager import JobManager
from telegram_phone_number_checker.models import CheckStatus
from telegram_phone_number_checker.repositories.job_repository import JobRepository
from telegram_phone_number_checker.repositories.result_repository import ResultRepository
from telegram_phone_number_checker.repositories.persistence_repository import SecretBox
from telegram_phone_number_checker.webapi.account_manager import AccountManager
from telegram_phone_number_checker.webapi.sse import SSEHub


def test_abrupt_process_exit_is_recoverable_after_restart(tmp_path):
    db_path = tmp_path / "crash.db"
    project_root = Path(__file__).resolve().parents[1]
    code = r'''
import os, sys
from pathlib import Path
from telegram_phone_number_checker.database import Database
from telegram_phone_number_checker.models import JobStatus
from telegram_phone_number_checker.repositories.job_repository import JobRepository
from telegram_phone_number_checker.repositories.result_repository import ResultRepository
db = Database(Path(sys.argv[1]))
job_repo = JobRepository(db); result_repo = ResultRepository(db)
job_repo.create("crash-job", "crash", total_items=1, telegram_account_phone="+84999999999")
result_repo.insert("crash-job", "+84911111111", "+84911111111", 3)
item = db.execute("SELECT id FROM check_items WHERE job_id=?", ("crash-job",)).fetchone()
result_repo.mark_processing(item["id"])
job_repo.update_status("crash-job", JobStatus.RUNNING)
db.execute("UPDATE jobs SET worker_id=?, worker_lease_until=? WHERE id=?", ("dead-worker", "2000-01-01T00:00:00Z", "crash-job"))
db.commit()
os._exit(17)
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = str(project_root)
    proc = subprocess.run([sys.executable, "-c", code, str(db_path)], cwd=project_root, env=env)
    assert proc.returncode == 17

    db = Database(db_path)
    cfg = Config(); cfg.database_url = None; cfg.database_path = db_path
    cfg.api_phone_number = "+84999999999"; cfg.in_flight_recovery_grace_seconds = 0
    asyncio.run(JobManager(cfg, db).recover("crash-job"))
    assert JobRepository(db).get("crash-job").status.value == "PAUSED"
    item = db.execute("SELECT id FROM check_items WHERE job_id=?", ("crash-job",)).fetchone()
    assert ResultRepository(db).get(item["id"]).status == CheckStatus.IN_FLIGHT_UNKNOWN
    db.close()


def test_sql_telegram_session_survives_database_reopen(tmp_path):
    db_path = tmp_path / "session-restart.db"
    cfg = Config(); cfg.database_url = None; cfg.database_path = db_path
    cfg.api_id = "1"; cfg.api_hash = "hash"; cfg.api_phone_number = "+84999999999"

    db1 = Database(db_path)
    first = AccountManager(cfg, SSEHub(), db1, SecretBox("restart-master"))
    account = first._repo.get_default()
    first._session_store.save(account["phone"], "sql-session-placeholder")
    db1.close()

    db2 = Database(db_path)
    second = AccountManager(cfg, SSEHub(), db2, SecretBox("restart-master"))
    account2 = second._repo.get_by_phone(account["phone"])
    assert account2 is not None
    assert second._session_backend(account2["phone"]) == "SQL"
    assert second._session_store.load(account2["phone"]) == "sql-session-placeholder"
    db2.close()
