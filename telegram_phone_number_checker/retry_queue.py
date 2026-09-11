import random
from typing import Optional

from .models import CheckItem, CheckStatus, ErrorType, iso_from_offset
from .repositories.result_repository import ResultRepository

#: Backward-compatible alias.
iso_future = iso_from_offset

#: Retry policy equation lives here so it is not scattered across files.
RETRY_EXHAUSTED_ERROR = ErrorType.RETRY_EXHAUSTED


def calculate_next_retry_delay(
    attempt: int,
    server_retry_after: Optional[int] = None,
    base_delay: int = 30,
    max_delay: int = 3600,
    jitter: bool = True,
) -> int:
    """Central retry delay policy (Phase 6).

    - If Telegram provided a server retry_after, use it verbatim (it is the
      authoritative delay; never shorten it with config or jitter).
    - Otherwise exponential backoff: min(max_delay, base * 2**(attempt-1))
      plus optional jitter.
    """
    if server_retry_after is not None and server_retry_after > 0:
        return int(server_retry_after)
    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
    if jitter:
        delay = max(0, int(delay * random.uniform(0.5, 1.0)))
    return delay


def compute_backoff(
    attempt: int,
    base_delay: int = 30,
    max_delay: int = 3600,
    jitter: bool = True,
) -> int:
    """Backward-compatible wrapper around the central policy."""
    return calculate_next_retry_delay(
        attempt,
        server_retry_after=None,
        base_delay=base_delay,
        max_delay=max_delay,
        jitter=jitter,
    )


class RetryQueue:
    """Database-backed retry queue.

    Items eligible for retry are returned by the query in ResultRepository.
    """

    def __init__(
        self, repo: ResultRepository, max_attempts: int, base_delay: int, max_delay: int
    ):
        self._repo = repo
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._max_delay = max_delay

    def next(self, job_id: str) -> Optional[CheckItem]:
        return self._repo.next_due_item(job_id)

    def is_exhausted(self, item: CheckItem) -> bool:
        return item.attempt_count >= item.max_attempts

    def schedule_retry(
        self,
        item: CheckItem,
        *,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        retry_after_seconds: Optional[int] = None,
        processing_token: Optional[str] = None,
        job_id: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> bool:
        # The just-completed failed attempt is number (attempt_count + 1).
        # Always record that this attempt happened first.
        new_attempt = item.attempt_count + 1

        # item.max_attempts is the source of truth for THIS item (captured at
        # creation time). Config MAX_ATTEMPTS is only used to seed new items, so
        # a config change mid-run does not alter already-created items.
        item_max = item.max_attempts or self._max_attempts

        # If this was the last allowed attempt, exhaust permanently so we
        # never run more than max_attempts total.
        if new_attempt >= item_max:
            return self._repo.save_result(
                item.id,
                CheckStatus.PERMANENT_ERROR,
                attempt_count=new_attempt,
                last_error_type=str(ErrorType.RETRY_EXHAUSTED.value),
                last_error_message="Maximum retry attempts reached",
                completed=True,
                processing_token=processing_token,
                job_id=job_id,
                worker_id=worker_id,
            )

        delay = calculate_next_retry_delay(
            new_attempt,
            server_retry_after=retry_after_seconds,
            base_delay=self._base_delay,
            max_delay=self._max_delay,
        )
        next_retry_at = iso_from_offset(delay)
        return self._repo.mark_retry(
            item.id,
            attempt_count=new_attempt,
            next_retry_at=next_retry_at,
            error_type=error_type,
            error_message=error_message,
            processing_token=processing_token,
            job_id=job_id,
            worker_id=worker_id,
        )
