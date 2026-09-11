import os
from typing import Any

from ..config import Config
from ..repositories.persistence_repository import SecretBox, SettingsRepository

SENSITIVE = {"API_ID", "API_HASH", "PROXY"}

CONFIG_KEYS = {
    "API_ID": ("api_id", str),
    "API_HASH": ("api_hash", str),
    "PROXY": ("proxy", str),
    "MAX_ATTEMPTS": ("max_attempts", int),
    "BASE_RETRY_DELAY_SECONDS": ("base_retry_delay_seconds", int),
    "MAX_RETRY_DELAY_SECONDS": ("max_retry_delay_seconds", int),
    "MIN_REQUEST_INTERVAL_SECONDS": ("min_request_interval_seconds", float),
    "AUTO_RESUME": ("auto_resume", lambda v: str(v).lower() == "true"),
    "DEFAULT_PHONE_REGION": ("default_phone_region", str),
    "WORKER_STALE_TIMEOUT_SECONDS": ("worker_stale_timeout_seconds", int),
    "WORKER_LEASE_SECONDS": ("worker_lease_seconds", int),
    "LEASE_RENEW_FAILURE_LIMIT": ("lease_renew_failure_limit", int),
    "LEASE_TAKEOVER_GRACE_SECONDS": ("lease_takeover_grace_seconds", int),
    "IN_FLIGHT_RECOVERY_GRACE_SECONDS": ("in_flight_recovery_grace_seconds", int),
}

AUTH_KEYS = (
    "WEB_UI_USERNAME",
    "WEB_UI_PASSWORD_SCRYPT",
    "WEB_UI_PASSWORD_HASH",
    "WEB_UI_SESSION_MAX_AGE_SECONDS",
    "WEB_UI_COOKIE_SECURE",
    "WEB_UI_LOGIN_FAILURE_LIMIT",
    "WEB_UI_LOGIN_WINDOW_SECONDS",
    "WEB_UI_LOGIN_BLOCK_SECONDS",
)


def bootstrap_and_apply_sql_state(db, config: Config):
    master = os.getenv("PERSISTENCE_MASTER_KEY")
    if not master:
        if getattr(db, "_use_postgres", False):
            raise RuntimeError("PERSISTENCE_MASTER_KEY is required for production SQL persistence.")
        master = os.getenv("WEB_UI_SECRET_KEY", "local-test-master-key")
    box = SecretBox(master)
    settings = SettingsRepository(db, box)

    for key, (attr, _converter) in CONFIG_KEYS.items():
        if settings.get(key) is not None:
            continue
        value = getattr(config, attr, None)
        if value is None:
            value = os.getenv(key)
        if value is not None:
            settings.set(key, str(value).lower() if isinstance(value, bool) else str(value), encrypted=key in SENSITIVE)

    for key in AUTH_KEYS:
        if settings.get(key) is None and os.getenv(key) is not None:
            settings.set(key, os.getenv(key), encrypted=False)

    for key, (attr, converter) in CONFIG_KEYS.items():
        value = settings.get(key)
        if value is None:
            continue
        if key == "MIN_REQUEST_INTERVAL_SECONDS" and value == "None":
            setattr(config, attr, None)
            continue
        setattr(config, attr, converter(value))

    return box, settings
