import pytest

from telegram_phone_number_checker.webapi.account_manager import _friendly_login_error
from telegram_phone_number_checker.webapi.runner import _friendly_job_error


@pytest.mark.parametrize(
    "error_name, expected",
    [
        ("PhoneCodeInvalidError", "OTP Telegram không đúng"),
        ("PhoneCodeExpiredError", "OTP Telegram đã hết hạn"),
        ("PasswordHashInvalidError", "2FA Telegram không đúng"),
        ("PhoneNumberInvalidError", "Số điện thoại Telegram cấu hình không hợp lệ"),
        ("ApiIdInvalidError", "API_ID/API_HASH Telegram không hợp lệ"),
        ("FloodWaitError", "giới hạn tốc độ đăng nhập"),
    ],
)
def test_login_errors_are_operator_friendly(error_name, expected):
    error_type = type(error_name, (Exception,), {})
    assert expected in _friendly_login_error(error_type())


def test_login_network_error_is_operator_friendly():
    assert "Kiểm tra mạng/proxy" in _friendly_login_error(TimeoutError())


@pytest.mark.parametrize(
    "exc, expected",
    [
        (TimeoutError(), "Mất kết nối"),
        (type("FloodWaitError", (Exception,), {})(), "giới hạn tốc độ"),
        (type("OperationalError", (Exception,), {})(), "Database gặp lỗi"),
    ],
)
def test_job_errors_are_operator_friendly(exc, expected):
    assert expected in _friendly_job_error(exc)
