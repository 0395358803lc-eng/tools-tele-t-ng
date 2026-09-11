import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class CheckStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"

    # A Telegram request may have reached the server, but this worker lost its
    # lease before it could safely commit the response.  This is deliberately
    # separate from the ordinary retry queue so it cannot be retried until the
    # in-flight recovery grace has elapsed.
    IN_FLIGHT_UNKNOWN = "IN_FLIGHT_UNKNOWN"

    FOUND = "FOUND"

    NOT_DISCOVERABLE = "NOT_DISCOVERABLE"

    RETRY_REQUIRED = "RETRY_REQUIRED"

    RATE_LIMITED = "RATE_LIMITED"

    TEMPORARY_ERROR = "TEMPORARY_ERROR"

    PERMANENT_ERROR = "PERMANENT_ERROR"

    COMPLETED = "COMPLETED"


class JobStatus(str, enum.Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RATE_LIMITED = "RATE_LIMITED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobCommand(str, enum.Enum):
    NONE = "NONE"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    CANCEL = "CANCEL"


#: Statuses that mean an item is fully settled and must never be re-processed.
#: Anything in PENDING/PROCESSING/RETRY_REQUIRED/TEMPORARY_ERROR/RATE_LIMITED
#: is NOT terminal.
TERMINAL_STATUSES = {
    CheckStatus.FOUND,
    CheckStatus.NOT_DISCOVERABLE,
    CheckStatus.PERMANENT_ERROR,
}

#: Statuses that are still in progress and must prevent a job from being
#: marked COMPLETED.
NON_TERMINAL_STATUSES = {
    CheckStatus.PENDING,
    CheckStatus.PROCESSING,
    CheckStatus.IN_FLIGHT_UNKNOWN,
    CheckStatus.RETRY_REQUIRED,
    CheckStatus.TEMPORARY_ERROR,
    CheckStatus.RATE_LIMITED,
}


class ErrorType(str, enum.Enum):
    NONE = "NONE"
    WORKER_INTERRUPTED = "WORKER_INTERRUPTED"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    INVALID_PHONE = "INVALID_PHONE"
    INVALID_FORMAT = "INVALID_FORMAT"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    CONNECTION_RESET = "CONNECTION_RESET"
    TELEGRAM_ERROR = "TELEGRAM_ERROR"
    DATABASE_LOCKED = "DATABASE_LOCKED"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    FLOOD_WAIT = "FLOOD_WAIT"
    UNKNOWN = "UNKNOWN"


@dataclass
class CheckResponse:
    status: CheckStatus
    phone: str
    telegram_user_id: Optional[int] = None
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    user_was_online: Optional[str] = None
    retry_after_seconds: Optional[int] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    cleanup_error: Optional[str] = None


@dataclass
class CheckItem:
    id: Optional[int]
    job_id: str
    original_phone: str
    normalized_phone: Optional[str] = None
    status: CheckStatus = CheckStatus.PENDING
    attempt_count: int = 0
    max_attempts: int = 5
    next_retry_at: Optional[str] = None
    last_error_type: Optional[str] = None
    last_error_message: Optional[str] = None
    telegram_user_id: Optional[int] = None
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    user_was_online: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    in_flight_started_at: Optional[str] = None
    ownership_lost_at: Optional[str] = None
    recovery_after: Optional[str] = None
    processing_token: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def masked_phone(self) -> str:
        return mask_phone(self.original_phone)


@dataclass
class Job:
    id: str
    name: Optional[str] = None
    status: JobStatus = JobStatus.CREATED
    total_items: int = 0
    processed_items: int = 0
    found_items: int = 0
    retry_items: int = 0
    failed_items: int = 0
    not_discoverable_items: int = 0
    requested_command: str = "NONE"
    worker_heartbeat_at: Optional[str] = None
    worker_id: Optional[str] = None
    worker_lease_until: Optional[str] = None
    last_error_type: Optional[str] = None
    last_error_message: Optional[str] = None
    pause_requested_at: Optional[str] = None
    paused_at: Optional[str] = None
    worker_started_at: Optional[str] = None
    telegram_account_phone: Optional[str] = None
    job_mode: str = "SINGLE"
    parent_job_id: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    updated_at: Optional[str] = None


def now_iso() -> str:
    from datetime import timezone

    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def parse_iso(value: str):
    """Parse an ISO-8601 UTC string (as produced by now_iso) into an aware
    datetime. Returns None for None/empty input."""
    from datetime import timezone

    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def seconds_until_iso(value: str) -> float:
    """Seconds from now until the given ISO UTC timestamp (0 if already past)."""
    from datetime import timezone

    target = parse_iso(value)
    if target is None:
        return 0.0
    now = datetime.now(timezone.utc)
    return max(0.0, (target - now).total_seconds())


def seconds_since_iso(value: str) -> float:
    """Seconds that have elapsed since the given ISO UTC timestamp (0 if empty
    or in the future). Unlike negating `seconds_until_iso`, this returns the
    correct positive age for heartbeat-age checks."""
    from datetime import timezone

    target = parse_iso(value)
    if target is None:
        return 0.0
    now = datetime.now(timezone.utc)
    return max(0.0, (now - target).total_seconds())


def iso_from_offset(delay_seconds: int) -> str:
    """ISO UTC timestamp 'delay_seconds' from now."""
    from datetime import timedelta, timezone

    utc = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
    return utc.isoformat(timespec="seconds").replace("+00:00", "Z")


def mask_phone(phone: str) -> str:
    digits = [c for c in phone if c.isdigit()]
    if len(digits) <= 4:
        return phone
    prefix = phone[: len(phone) - 4]
    return prefix + "****" + phone[-4:]
