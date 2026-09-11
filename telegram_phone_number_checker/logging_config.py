import json
import logging
import sys
from typing import Any, Dict

from .models import mask_phone

RESERVED_KEYS = {"API_HASH", "API_ID", "PHONE_NUMBER", "password", "session", "token"}
SECRET_SUBSTRINGS = ("HASH", "PASSWORD", "OTP", "SECRET", "TOKEN", "SESSION")


class StructuredLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        extra = getattr(record, "extra", {})
        if isinstance(extra, dict):
            base.update(sanitize_fields(extra))
        return json.dumps(base, ensure_ascii=False)


def sanitize_fields(extra: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = {}
    for k, v in extra.items():
        key = str(k).upper()
        # Drop anything that looks like a secret or carries an auth value.
        if key in RESERVED_KEYS or any(s in key for s in SECRET_SUBSTRINGS):
            continue
        # Any key that smells like a phone gets masked (PII).
        if "PHONE" in key and v is not None:
            sanitized[k] = mask_phone(str(v))
        else:
            sanitized[k] = v
    return sanitized


def add_extra(record: logging.LogRecord, extra: Dict[str, Any]) -> None:
    record.extra = sanitize_fields(extra)


def log_event(
    logger: logging.Logger, event: str, level: int = logging.INFO, **extra
) -> None:
    if logger.isEnabledFor(level):
        record = logging.LogRecord(
            name=logger.name,
            level=level,
            pathname=__file__,
            lineno=1,
            msg=event,
            args=(),
            exc_info=None,
        )
        add_extra(record, extra)
        logger.handle(record)


def setup_logging(level: str = "INFO") -> logging.Logger:
    root = logging.getLogger("telegram_phone_number_checker")
    root.setLevel(level.upper())
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredLogFormatter())
    root.addHandler(handler)
    root.propagate = False

    telethon_logger = logging.getLogger("telethon")
    telethon_logger.setLevel(logging.WARNING)
    if not any(isinstance(h, logging.StreamHandler) for h in telethon_logger.handlers):
        telethon_logger.addHandler(logging.NullHandler())

    return root
