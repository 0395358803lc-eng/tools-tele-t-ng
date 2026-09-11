from typing import Optional

from .models import CheckItem, CheckStatus, iso_from_offset
from .repositories.result_repository import ResultRepository


class CheckpointManager:
    """Checkpoint every phone directly in the database.

    Each phone is its own independent checkpoint. On restart we only re-pick
    items that are PENDING or whose retry time has elapsed. Completed terminal
    statuses (FOUND, NOT_DISCOVERABLE, PERMANENT_ERROR) are never re-processed.
    """

    TERMINAL_STATUSES = {
        CheckStatus.FOUND,
        CheckStatus.NOT_DISCOVERABLE,
        CheckStatus.PERMANENT_ERROR,
    }

    def __init__(self, repo: ResultRepository):
        self._repo = repo

    def mark_processing(
        self,
        item: CheckItem,
        job_id: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> Optional[str]:
        return self._repo.mark_processing(item.id, job_id, worker_id)

    def is_terminal(self, status: CheckStatus) -> bool:
        return status in self.TERMINAL_STATUSES

    def next_item(self, job_id: str) -> Optional[CheckItem]:
        return self._repo.next_due_item(job_id)

    def recover_interrupted(self, job_id: str, recovery_grace_seconds: int = 60) -> int:
        """Quarantine leftover PROCESSING items before controlled retry."""
        return self._repo.recover_interrupted(
            job_id, iso_from_offset(recovery_grace_seconds)
        )

    def count_pending(self, job_id: str) -> int:
        return self._repo.count_pending_all_statuses(job_id)

    def has_unfinished_items(self, job_id: str) -> bool:
        """True if any item is not in a terminal state (blocks COMPLETED)."""
        return self._repo.has_unfinished_items(job_id)

    def get_next_retry_at(self, job_id: str) -> Optional[str]:
        """Earliest future retry timestamp for retryable items, or None."""
        return self._repo.get_next_retry_at(job_id)
