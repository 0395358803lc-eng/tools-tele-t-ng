import pytest

from telegram_phone_number_checker.phone_utils import (
    PhoneNormalizationError,
    is_valid_phone,
    normalize_phone,
)


def test_normalize_plus_e164():
    assert normalize_phone("+84 911 234 567", default_region="VN") == "+84911234567"
    assert normalize_phone("+84911234567") == "+84911234567"


def test_normalize_without_country_code():
    # Vietnamese number without +84
    assert normalize_phone("0911234567", default_region="VN") == "+84911234567"


def test_invalid_phone_raises():
    with pytest.raises(PhoneNormalizationError):
        normalize_phone("not-a-phone")
    with pytest.raises(PhoneNormalizationError):
        normalize_phone("")


def test_is_valid_phone():
    assert is_valid_phone("+84911234567") is True
    assert is_valid_phone("garbage") is False


def test_international_numbers_independent_of_region():
    # Numbers with '+' parse in E.164 regardless of the default region.
    assert normalize_phone("+14155552671", default_region="VN") == "+14155552671"
    assert normalize_phone("+447911123456", default_region="VN") == "+447911123456"


def test_default_region_used_without_plus():
    # A number without '+' uses the default region to disambiguate.
    assert normalize_phone("0911234567", default_region="VN") == "+84911234567"


def test_different_default_regions_yield_valid_e164():
    # Without '+', region changes the result; the formatter still validates.
    us_local = "4155552671"  # valid 10-digit US local number
    assert normalize_phone(us_local, default_region="US") == "+14155552671"
