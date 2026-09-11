from typing import Optional

import phonenumbers


class PhoneNormalizationError(ValueError):
    pass


def normalize_phone(raw: str, default_region: str = None) -> str:
    """Normalize a phone number to E.164 international format.

    Raises PhoneNormalizationError if the number cannot be parsed/validated.
    """
    if raw is None:
        raise PhoneNormalizationError("Phone number is empty")

    value = raw.strip()
    if not value:
        raise PhoneNormalizationError("Phone number is empty")

    try:
        if value.startswith("+"):
            parsed = phonenumbers.parse(value, None)
        else:
            region = default_region or "VN"
            parsed = phonenumbers.parse(value, region)
    except phonenumbers.NumberParseException as e:
        raise PhoneNormalizationError(f"Could not parse phone number: {e}") from e

    if not phonenumbers.is_valid_number(parsed):
        raise PhoneNormalizationError(f"Invalid phone number: {value}")

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def is_valid_phone(raw: str) -> bool:
    try:
        normalize_phone(raw)
        return True
    except PhoneNormalizationError:
        return False
