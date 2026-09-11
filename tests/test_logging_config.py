import json
import logging

from telegram_phone_number_checker.logging_config import StructuredLogFormatter
from telegram_phone_number_checker.models import mask_phone


def test_mask_phone():
    assert mask_phone("+84912345678") == "+8491234****5678"
    assert mask_phone("abc") == "abc"


def test_structured_formatter_masks_phone_extra():
    fmt = StructuredLogFormatter()
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="PHONE_CHECK_COMPLETED",
        args=(),
        exc_info=None,
    )
    # add extra via the module's helper to exercise sanitization
    from telegram_phone_number_checker.logging_config import add_extra

    add_extra(record, {"phone": "+84912345678", "event": "x"})
    record.extra = {"phone": "+84912345678", "job_id": "j1"}
    out = fmt.format(record)
    parsed = json.loads(out)
    assert parsed["phone"] == "+8491234****5678"
    assert "849123456789" not in parsed["phone"]


def test_logger_redacts_sensitive_keys():
    from telegram_phone_number_checker.logging_config import add_extra

    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="x",
        args=(),
        exc_info=None,
    )
    add_extra(
        record,
        {"api_hash": "secret", "otp": "123", "PHONE_NUMBER": "+849", "job_id": "j"},
    )
    assert "job_id" in record.extra
    assert "api_hash" not in record.extra
    assert "otp" not in record.extra
    assert "PHONE_NUMBER" not in record.extra


def test_logger_redacts_session_and_token():
    from telegram_phone_number_checker.logging_config import add_extra

    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="x",
        args=(),
        exc_info=None,
    )
    add_extra(
        record,
        {
            "session": "abcdef",
            "session_path": "/tmp/x.session",
            "api_id": "12345",
            "token": "tok",
        },
    )
    assert "session" not in record.extra
    assert "session_path" not in record.extra
    assert "api_id" not in record.extra
    assert "token" not in record.extra


def test_any_phone_key_is_masked():
    from telegram_phone_number_checker.logging_config import add_extra

    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="x",
        args=(),
        exc_info=None,
    )
    add_extra(record, {"callback_phone": "+84912345678", "home_phone": "+84987654321"})
    assert record.extra["callback_phone"] == "+8491234****5678"
    assert record.extra["home_phone"] == "+8498765****4321"
