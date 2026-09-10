from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    TG_API_ID: int = 0
    TG_API_HASH: str = ""
    SESSIONS_DIR: str = "./sessions"
    DATABASE_URL: str = ""
    RATE_MIN: float = 0.7
    RATE_MAX: float = 1.5
    RECIPIENT_SEND_DELAY_MIN: float = 3.0
    RECIPIENT_SEND_DELAY_MAX: float = 7.0
    CONCURRENCY: int = 8  # how many accounts a bulk task processes in parallel
    STARTUP_CONCURRENCY: int = 10  # how many accounts connect in parallel on boot
    ALLOWED_ORIGIN: str = "http://localhost:5173"

    APP_PASSWORD: str = ""
    SESSION_SECRET: str = ""
    SESSION_DAYS: int = 14
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_WINDOW_MIN: int = 15
    COOKIE_SECURE: bool = True
    TWOFA_ENCRYPTION_KEY: str = ""
    MAX_SESSION_UPLOAD_BYTES: int = 8 * 1024 * 1024
    MAX_IMAGE_UPLOAD_BYTES: int = 15 * 1024 * 1024
    MAX_RECIPIENT_UPLOAD_BYTES: int = 10 * 1024 * 1024
    MAX_RECIPIENTS_PER_FILE: int = 5000
    ACTION_TIMEOUT: float = 90.0

    @property
    def sessions_path(self) -> Path:
        p = Path(self.SESSIONS_DIR)
        p.mkdir(parents=True, exist_ok=True)
        try:
            p.chmod(0o700)
        except OSError:
            pass
        return p

    @property
    def database_url(self) -> str:
        value = self.DATABASE_URL.strip()
        if value.startswith("postgres://"):
            value = "postgresql://" + value[len("postgres://") :]
        if value.startswith("postgresql://"):
            value = "postgresql+asyncpg://" + value[len("postgresql://") :]
        if not value.startswith("postgresql+asyncpg://"):
            raise RuntimeError(
                "DATABASE_URL must be a PostgreSQL URL using the managed SQL database."
            )
        parsed = urlsplit(value)
        query = [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if key != "sslmode"
        ]
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query),
                parsed.fragment,
            )
        )

    @property
    def database_connect_args(self) -> dict[str, object]:
        parsed = urlsplit(
            self.DATABASE_URL.strip().replace("postgres://", "postgresql://", 1)
        )
        sslmode = dict(parse_qsl(parsed.query, keep_blank_values=True)).get("sslmode")
        if sslmode in {"require", "verify-ca", "verify-full"}:
            return {"ssl": True}
        return {}


settings = Settings()
