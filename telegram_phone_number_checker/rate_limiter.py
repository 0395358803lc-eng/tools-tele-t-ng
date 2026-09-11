import asyncio
import hashlib
import time
from datetime import datetime, timezone
from typing import Optional

from .database import Database
from .models import now_iso, parse_iso


def account_key_from_phone(phone_number: str) -> str:
    """Non-secret key for a Telegram account used as the row key in
    account_runtime_state. It is a hash of the identifier, never a secret."""
    return hashlib.sha256(phone_number.encode("utf-8")).hexdigest()[:16]


def _to_epoch(iso_value) -> Optional[float]:
    dt = parse_iso(iso_value)
    return dt.timestamp() if dt is not None else None


class RateLimitManager:
    """Paced request limiter driven primarily by Telegram's own responses.

    Never hardcodes daily/request quotas. Telegram's FloodWait response is the
    authoritative source, augmented by an optional safety minimum spacing
    between requests (MIN_REQUEST_INTERVAL_SECONDS) which is not a rate-limit
    workaround but conservative pacing.

    The block state is persisted to the account_runtime_state table so a
    FloodWait cooldown survives a restart.
    """

    def __init__(
        self,
        min_request_interval_seconds: Optional[float] = None,
        db: Optional[Database] = None,
        account_key: Optional[str] = None,
    ):
        self._min_interval = min_request_interval_seconds
        self._db = db
        self._account_key = account_key
        self._last_request_at: Optional[float] = None
        # Wall-clock epoch seconds when the block expires (None = not blocked).
        self._blocked_until: Optional[float] = None
        self._lock = asyncio.Lock()

    def initialize(self) -> None:
        """Load any persisted cooldown from the previous process run."""
        self._reload_from_db()

    def _reload_from_db(self) -> None:
        """Re-read the persisted cooldown from the DB. Called on startup AND
        before each acquire so a cooldown written by another process (Job B
        after Job A hit FloodWait) is honored without a restart."""
        if self._db is None or self._account_key is None:
            return
        row = self._db.execute(
            """
            SELECT blocked_until FROM account_runtime_state
            WHERE account_key = ?
            """,
            (self._account_key,),
        ).fetchone()
        if row and row["blocked_until"]:
            epoch = _to_epoch(row["blocked_until"])
            if epoch is not None and epoch > time.time():
                self._blocked_until = epoch
                return
        # Nothing persisted (or already expired). Only *update* when our loaded
        # value is also expired/None, so a live in-memory block is not cleared
        # by an empty DB read.
        if self._blocked_until is None or time.time() >= self._blocked_until:
            self._blocked_until = None

    def is_blocked(self) -> bool:
        if self._blocked_until is None:
            return False
        if time.time() < self._blocked_until:
            return True
        self._blocked_until = None
        self._persist(clear=True)
        return False

    def available_at(self) -> Optional[datetime]:
        if self.is_blocked():
            return datetime.fromtimestamp(self._blocked_until, tz=timezone.utc)
        return None

    def remaining_block_seconds(self) -> float:
        if self._blocked_until is None:
            return 0.0
        remaining = self._blocked_until - time.time()
        return max(0.0, remaining)

    async def acquire(self) -> None:
        """Block until we are allowed to issue a request.

        Re-reads the persisted cooldown from the DB first so a FloodWait
        written by another process is honored even if this worker did not
        observe the original rate-limit response.
        """
        async with self._lock:
            self._reload_from_db()
            while self._blocked_until is not None and time.time() < self._blocked_until:
                await asyncio.sleep(min(self._blocked_until - time.time(), 1.0))
            if self._blocked_until is not None:
                self._blocked_until = None
                self._persist(clear=True)

            if self._min_interval is not None and self._last_request_at is not None:
                elapsed = time.time() - self._last_request_at
                wait = self._min_interval - elapsed
                if wait > 0:
                    await asyncio.sleep(wait)

    def register_success(self, cooldown_after_request_seconds: float = 0.0) -> None:
        self._last_request_at = time.time()

    def register_rate_limit(self, seconds: int) -> None:
        """Set a global cooldown; no new Telegram requests while active. This
        is persisted so the cooldown survives a restart."""
        self._blocked_until = time.time() + max(0, seconds)
        self._persist()

    def _persist(self, clear: bool = False) -> None:
        if self._db is None or self._account_key is None:
            return
        now = now_iso()
        if clear:
            self._db.execute(
                """
                INSERT INTO account_runtime_state
                    (account_key, blocked_until, updated_at)
                VALUES (?, NULL, ?)
                ON CONFLICT(account_key) DO UPDATE SET
                    blocked_until = NULL, updated_at = excluded.updated_at
                """,
                (self._account_key, now),
            )
            self._db.commit()
            return
        # Persist the exact expiry time (epoch -> ISO UTC) so the cooldown
        # survives a restart with the correct remaining duration.
        banned_iso = None
        if self._blocked_until is not None:
            from datetime import datetime, timezone

            banned_iso = (
                datetime.fromtimestamp(self._blocked_until, tz=timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
        self._db.execute(
            """
            INSERT INTO account_runtime_state
                (account_key, blocked_until, last_rate_limit_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_key) DO UPDATE SET
                blocked_until = excluded.blocked_until,
                updated_at = excluded.updated_at
            """,
            (self._account_key, banned_iso, now, now),
        )
        self._db.commit()
