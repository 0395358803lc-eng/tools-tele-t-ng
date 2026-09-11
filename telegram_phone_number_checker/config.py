import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .database_config import resolved_database_url

load_dotenv()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_float(name: str, default: Optional[float]) -> Optional[float]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


@dataclass
class Config:
    api_id: Optional[str] = field(default_factory=lambda: os.getenv("API_ID"))
    api_hash: Optional[str] = field(default_factory=lambda: os.getenv("API_HASH"))
    api_phone_number: Optional[str] = field(
        default_factory=lambda: os.getenv("PHONE_NUMBER")
    )
    proxy: Optional[str] = field(default_factory=lambda: os.getenv("PROXY"))
    database_url: Optional[str] = field(
        default_factory=resolved_database_url
    )

    database_path: Path = field(
        default_factory=lambda: Path(os.getenv("DATABASE_PATH", "data/checker.db"))
    )
    max_attempts: int = field(default_factory=lambda: _env_int("MAX_ATTEMPTS", 5))
    base_retry_delay_seconds: int = field(
        default_factory=lambda: _env_int("BASE_RETRY_DELAY_SECONDS", 30)
    )
    max_retry_delay_seconds: int = field(
        default_factory=lambda: _env_int("MAX_RETRY_DELAY_SECONDS", 3600)
    )
    min_request_interval_seconds: Optional[float] = field(
        default_factory=lambda: _env_float("MIN_REQUEST_INTERVAL_SECONDS", None)
    )
    auto_resume: bool = field(
        default_factory=lambda: os.getenv("AUTO_RESUME", "false").lower() == "true"
    )
    default_phone_region: str = field(
        default_factory=lambda: os.getenv("DEFAULT_PHONE_REGION", "VN")
    )
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    output_dir: Path = field(default_factory=lambda: Path("data"))
    # A worker is considered dead (stale) if its heartbeat is older than this.
    worker_stale_timeout_seconds: int = field(
        default_factory=lambda: _env_int("WORKER_STALE_TIMEOUT_SECONDS", 30)
    )
    # A worker's claim lease expires after this many seconds without renewal.
    worker_lease_seconds: int = field(
        default_factory=lambda: _env_int("WORKER_LEASE_SECONDS", 60)
    )
    # How many consecutive heartbeat/renew failures before the worker declares
    # its lease unsafe and fails closed (P0-12). Total retry window must not
    # exceed the lease expiry, so keep this modest (default 3).
    lease_renew_failure_limit: int = field(
        default_factory=lambda: _env_int("LEASE_RENEW_FAILURE_LIMIT", 3)
    )
    # Extra wait after a lease expires before a *new* worker may take it over
    # (P0-17). Pure concurrency safety: reduces the chance of a takeover landing
    # while the old worker is finishing a network operation. Not a rate-limit
    # workaround.
    lease_takeover_grace_seconds: int = field(
        default_factory=lambda: _env_int("LEASE_TAKEOVER_GRACE_SECONDS", 0)
    )
    # Quarantine interval for an item whose Telegram request was in flight
    # when ownership was lost.  It must not return to the selectable queue
    # before this deadline.
    in_flight_recovery_grace_seconds: int = field(
        default_factory=lambda: _env_int("IN_FLIGHT_RECOVERY_GRACE_SECONDS", 60)
    )

    @property
    def db_path(self) -> Path:
        return self.database_path

    @classmethod
    def from_cli(
        cls,
        api_id: Optional[str] = None,
        api_hash: Optional[str] = None,
        api_phone_number: Optional[str] = None,
        proxy: Optional[str] = None,
        database_path: Optional[str] = None,
        **kwargs
    ) -> "Config":
        cfg = cls()
        cfg.api_id = api_id or cfg.api_id
        cfg.api_hash = api_hash or cfg.api_hash
        cfg.api_phone_number = api_phone_number or cfg.api_phone_number
        cfg.proxy = proxy or cfg.proxy
        if database_path:
            cfg.database_path = Path(database_path)
            # An explicit --db path is a deliberate local override.
            cfg.database_url = None
        return cfg

    def ensure_dirs(self) -> None:
        if self.database_path.parent:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if self.output_dir:
            self.output_dir.mkdir(parents=True, exist_ok=True)
