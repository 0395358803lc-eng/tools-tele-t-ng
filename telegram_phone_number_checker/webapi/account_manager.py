"""SQL-backed multi-account Telegram session management for the Web UI."""

import asyncio
import logging
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

from telethon import TelegramClient, errors
from telethon.sessions import StringSession

import phonenumbers

from ..config import Config
from ..logging_config import log_event
from ..models import mask_phone, now_iso
from ..rate_limiter import RateLimitManager, account_key_from_phone
from ..repositories.account_repository import AccountRepository
from ..repositories.job_repository import JobRepository
from ..repositories.persistence_repository import (
    SecretBox,
    TelegramLoginRepository,
)
from ..telegram_service import TelegramService, parse_proxy
from ..session_importer import SessionImportError, inspect_and_convert_session
from ..telegram_session_store import SqlTelegramSessionStore
from .sse import SSEHub

logger = logging.getLogger(__name__)


class LoginState(str, Enum):
    WAIT_CODE = "WAIT_CODE"
    WAIT_2FA = "WAIT_2FA"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

def _friendly_login_error(exc: Exception) -> str:
    name = type(exc).__name__
    messages = {
        "PhoneCodeInvalidError": "Mã OTP Telegram không đúng.",
        "PhoneCodeExpiredError": "Mã OTP Telegram đã hết hạn.",
        "PasswordHashInvalidError": "Mật khẩu 2FA Telegram không đúng.",
        "PhoneNumberInvalidError": "Số điện thoại Telegram cấu hình không hợp lệ.",
        "ApiIdInvalidError": "API_ID/API_HASH Telegram không hợp lệ.",
        "AuthKeyError": "Phiên Telegram không còn hợp lệ; cần đăng nhập lại.",
        "FloodWaitError": "Telegram đang giới hạn tốc độ đăng nhập.",
    }
    if name in messages:
        return messages[name]
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return "Không thể kết nối Telegram. Kiểm tra mạng/proxy rồi thử lại."
    return f"Đăng nhập Telegram thất bại ({name})."


class AccountAlreadyBusy(RuntimeError):
    pass


class LoginSessionNotFound(RuntimeError):
    pass

class AccountManager:
    def __init__(self, config: Config, sse: SSEHub, db=None, secret_box: SecretBox | None = None):
        self._config = config
        self._sse = sse
        self._db = db
        self._job_repo = JobRepository(db) if db is not None else None
        self._repo = AccountRepository(db) if db is not None else None
        if db is not None and secret_box is None:
            import os
            secret_box = SecretBox(os.getenv("PERSISTENCE_MASTER_KEY") or os.getenv("WEB_UI_SECRET_KEY") or "local-test-master-key")
        self._box = secret_box
        self._login_repo = TelegramLoginRepository(db, secret_box) if db is not None and secret_box is not None else None
        self._session_store = SqlTelegramSessionStore(db, secret_box) if db is not None and secret_box is not None else None
        self._status_cache: dict[str, tuple[float, bool]] = {}
        self._status_cache_seconds = 30.0
        self._qr_pending: dict[str, dict] = {}
        self._management_locks: dict[str, asyncio.Lock] = {}
        self._management_limiters: dict[str, RateLimitManager] = {}
        self._bootstrap_configured_account()

    def _bootstrap_configured_account(self) -> None:
        if self._repo is None or not self._config.api_phone_number:
            return
        phone = self._normalize_phone(self._config.api_phone_number)
        if self._repo.get_by_phone(phone) is None:
            self._repo.create(uuid.uuid4().hex[:12], phone, "Tài khoản hiện tại")

    def _normalize_phone(self, phone: str) -> str:
        parsed = phonenumbers.parse(phone, self._config.default_phone_region)
        if not phonenumbers.is_valid_number(parsed):
            raise ValueError("Số điện thoại không hợp lệ.")
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

    def _account_in_use(self, phone: str) -> bool:
        if self._job_repo is None:
            return False
        return self._job_repo.is_account_owned(account_key_from_phone(phone))

    def _new_service(
        self,
        phone: Optional[str] = None,
        force_sql: bool = False,
        management: bool = False,
    ) -> TelegramService:
        if not self._config.api_id or not self._config.api_hash:
            raise ValueError("Chưa cấu hình API_ID/API_HASH Telegram.")
        effective_phone = phone or self.default_phone()
        if not effective_phone:
            raise ValueError("Chưa chọn số điện thoại Telegram.")
        kwargs = {}
        if self._session_store is not None:
            legacy_file = Path(f"{effective_phone}.session")
            use_sql = force_sql or self._session_store.has(effective_phone) or not legacy_file.exists()
            if use_sql:
                kwargs["session_loader"] = lambda: self._session_store.load(effective_phone)
                kwargs["session_saver"] = lambda value: self._session_store.save(effective_phone, value)
        if management and self._db is not None and self._job_repo is not None:
            account_key = account_key_from_phone(effective_phone)
            lock = self._management_locks.setdefault(account_key, asyncio.Lock())
            limiter = self._management_limiters.get(account_key)
            if limiter is None:
                limiter = RateLimitManager(
                    self._config.min_request_interval_seconds,
                    db=self._db,
                    account_key=account_key,
                )
                limiter.initialize()
                self._management_limiters[account_key] = limiter
            worker_id = f"manager-{uuid.uuid4().hex[:12]}"
            operation_id = f"manager-op-{uuid.uuid4().hex[:12]}"

            def claim_management_account() -> None:
                claimed = self._job_repo.claim_account(
                    account_key,
                    worker_id,
                    operation_id,
                    lease_seconds=max(300, int(self._config.worker_lease_seconds)),
                    takeover_grace_seconds=self._config.lease_takeover_grace_seconds,
                )
                if not claimed:
                    raise AccountAlreadyBusy(
                        "Tài khoản Telegram đang được một worker hoặc thao tác quản trị khác sử dụng."
                    )

            def release_management_account() -> None:
                self._job_repo.release_account(account_key, worker_id, operation_id)

            kwargs.update(
                management_rate_limiter=limiter,
                management_lock=lock,
                management_claim=claim_management_account,
                management_release=release_management_account,
            )
        return TelegramService(
            self._config.api_id,
            self._config.api_hash,
            effective_phone,
            proxy=self._config.proxy,
            **kwargs,
        )

    def _session_backend(self, phone: str) -> str:
        if self._session_store is not None and self._session_store.has(phone):
            return "SQL"
        if Path(f"{phone}.session").exists():
            return "LEGACY_FILE"
        return "NONE"

    def _remove_legacy_session_files(self, phone: str) -> None:
        for candidate in (Path(f"{phone}.session"), Path(f"{phone}.session-journal")):
            try:
                if candidate.exists():
                    candidate.unlink()
            except OSError as exc:
                logger.warning("Không thể xóa session legacy sau khi chuyển SQL: %s", exc)

    def default_phone(self) -> Optional[str]:
        account = self._repo.get_default() if self._repo else None
        return account["phone"] if account else self._config.api_phone_number

    def get_account(self, account_id: str) -> Optional[Dict]:
        return self._repo.get(account_id) if self._repo else None

    def management_service(self, account_id: str) -> TelegramService:
        """Return a short-lived management service using the canonical SQL session."""
        account = self.get_account(account_id)
        if account is None:
            raise ValueError("Không tìm thấy tài khoản Telegram.")
        if self._account_in_use(account["phone"]):
            raise AccountAlreadyBusy("Tài khoản đang được JobRunner sử dụng; hãy tạm dừng job trước.")
        return self._new_service(account["phone"], management=True)

    def phone_for(self, account_id: Optional[str] = None) -> Optional[str]:
        if self._repo is None:
            return self._config.api_phone_number
        account = self._repo.get(account_id) if account_id else self._repo.get_default()
        if account_id and account is None:
            raise ValueError("Không tìm thấy tài khoản Telegram.")
        return account["phone"] if account else None

    def _public_login(self, row: dict) -> dict:
        account = self._repo.get(row["account_id"]) if self._repo else None
        return {
            "session_id": row["session_id"],
            "account_id": row["account_id"],
            "phone": mask_phone(account["phone"]) if account else None,
            "state": row["state"],
            "error": row.get("error"),
        }

    def active_login(self, account_id: Optional[str] = None) -> Optional[dict]:
        if self._login_repo is None or self._repo is None:
            return None
        account = self._repo.get(account_id) if account_id else self._repo.get_default()
        if account is None:
            return None
        row = self._login_repo.active_for_account(account["id"])
        return self._public_login(row) if row else None

    async def _status_for(self, account: Dict) -> Dict:
        pending = self.active_login(account["id"])
        in_use = self._account_in_use(account["phone"])
        limiter = RateLimitManager(db=self._db, account_key=account_key_from_phone(account["phone"])) if self._db is not None else None
        blocked_until = None
        last_rate_limit_at = None
        if limiter is not None:
            limiter.initialize()
            available = limiter.available_at()
            blocked_until = available.isoformat(timespec="seconds").replace("+00:00", "Z") if available else None
            row = self._db.execute(
                "SELECT last_rate_limit_at FROM account_runtime_state WHERE account_key=?",
                (account_key_from_phone(account["phone"]),),
            ).fetchone()
            last_rate_limit_at = row["last_rate_limit_at"] if row else None

        if pending:
            state = "LOGIN_IN_PROGRESS"
        elif in_use:
            state = "IN_USE"
        elif blocked_until:
            state = "FLOOD_WAIT"
        else:
            cached = self._status_cache.get(account["id"])
            if cached and time.monotonic() - cached[0] <= self._status_cache_seconds:
                state = "AUTHORIZED" if cached[1] else "NOT_AUTHORIZED"
            else:
                try:
                    service = self._new_service() if account["phone"] == self._config.api_phone_number else self._new_service(account["phone"])
                    ok = await service.is_authorized()
                except Exception:
                    ok = False
                self._status_cache[account["id"]] = (time.monotonic(), ok)
                state = "AUTHORIZED" if ok else "NOT_AUTHORIZED"
        return {
            "id": account["id"], "label": account["label"],
            "phone": mask_phone(account["phone"]), "is_default": bool(account["is_default"]),
            "state": state, "last_login_at": account.get("last_login_at"), "login": pending,
            "session_backend": self._session_backend(account["phone"]), "in_use": in_use,
            "blocked_until": blocked_until, "last_rate_limit_at": last_rate_limit_at,
        }

    async def status_for_id(self, account_id: Optional[str] = None) -> Dict:
        if self._repo is None:
            return {"state": "NOT_CONFIGURED"}
        account = self._repo.get(account_id) if account_id else self._repo.get_default()
        if account is None:
            raise ValueError("Không tìm thấy tài khoản Telegram.")
        return await self._status_for(account)

    async def list_accounts(self) -> Dict:
        accounts = self._repo.list() if self._repo else []
        return {"items": [await self._status_for(a) for a in accounts], "total": len(accounts)}

    async def status(self) -> Dict:
        account = self._repo.get_default() if self._repo else None
        if account is None:
            return {"state": "NOT_CONFIGURED", "phone": None}
        return await self._status_for(account)

    def add_account(self, phone: str, label: Optional[str] = None) -> Dict:
        if self._repo is None:
            raise RuntimeError("Account repository chưa sẵn sàng.")
        normalized = self._normalize_phone(phone)
        existing = self._repo.get_by_phone(normalized)
        if existing:
            return existing
        return self._repo.create(uuid.uuid4().hex[:12], normalized, label)

    async def import_session_file(
        self, path: Path, label: Optional[str] = None, account_id: Optional[str] = None
    ) -> Dict:
        if self._repo is None or self._session_store is None:
            raise RuntimeError("SQL session repository chưa sẵn sàng.")
        if not self._config.api_id or not self._config.api_hash:
            raise ValueError("Chưa cấu hình API_ID/API_HASH Telegram.")
        target = self._repo.get(account_id) if account_id else None
        if account_id and target is None:
            raise ValueError("Không tìm thấy tài khoản Telegram đích.")
        if target and (self._account_in_use(target["phone"]) or (self._job_repo and self._job_repo.has_unfinished_jobs_for_account(target["phone"]))):
            raise AccountAlreadyBusy("Tài khoản đang được job sử dụng hoặc còn job chưa kết thúc.")
        result = await inspect_and_convert_session(
            path, self._config.api_id, self._config.api_hash,
            parse_proxy(self._config.proxy) if self._config.proxy else None,
        )
        raw_phone = result.get("phone")
        if not raw_phone and target is None:
            raise SessionImportError("Không xác định được số điện thoại từ session; hãy chọn account đích trước.")
        normalized = target["phone"] if target else self._normalize_phone("+" + str(raw_phone).lstrip("+"))
        if target and raw_phone:
            actual = self._normalize_phone("+" + str(raw_phone).lstrip("+"))
            if actual != target["phone"]:
                raise SessionImportError("Session không thuộc tài khoản Telegram đã chọn.")
        account = target or self._repo.get_by_phone(normalized)
        if account and (self._account_in_use(account["phone"]) or (self._job_repo and self._job_repo.has_unfinished_jobs_for_account(account["phone"]))):
            raise AccountAlreadyBusy("Tài khoản còn job chưa kết thúc; không thể thay session.")
        # Metadata + encrypted session + login marker are one SQL transaction.
        with self._db.transaction():
            if account is None:
                default_label = result.get("username") or result.get("first_name") or "Tài khoản import session"
                account = self._repo.create(uuid.uuid4().hex[:12], normalized, label or default_label)
            elif label:
                self._repo.rename(account["id"], label)
            self._session_store.save(account["phone"], result["session_string"])
            self._repo.mark_logged_in(account["id"])
        # Filesystem cleanup happens only after the SQL transaction commits.
        self._remove_legacy_session_files(account["phone"])
        self._status_cache[account["id"]] = (time.monotonic(), True)
        log_event(logger, "ACCOUNT_SESSION_IMPORTED", phone=mask_phone(account["phone"]))
        return await self._status_for(self._repo.get(account["id"]))

    async def migrate_legacy_session(self, account_id: str) -> Dict:
        if self._repo is None or self._session_store is None or self._db is None:
            raise RuntimeError("SQL session repository chưa sẵn sàng.")
        account = self._repo.get(account_id)
        if account is None:
            raise ValueError("Không tìm thấy tài khoản Telegram.")
        if self._account_in_use(account["phone"]):
            raise AccountAlreadyBusy("Tài khoản đang được job sử dụng.")
        legacy = Path(f"{account['phone']}.session")
        if not legacy.exists():
            raise ValueError("Không tìm thấy file session legacy để chuyển đổi.")
        result = await inspect_and_convert_session(
            legacy, self._config.api_id, self._config.api_hash,
            parse_proxy(self._config.proxy) if self._config.proxy else None,
        )
        raw_phone = result.get("phone")
        if raw_phone:
            actual = self._normalize_phone("+" + str(raw_phone).lstrip("+"))
            if actual != account["phone"]:
                raise SessionImportError("Session legacy không thuộc tài khoản đã chọn.")
        with self._db.transaction():
            self._session_store.save(account["phone"], result["session_string"])
            self._repo.mark_logged_in(account["id"])
        self._remove_legacy_session_files(account["phone"])
        self._status_cache[account["id"]] = (time.monotonic(), True)
        log_event(logger, "ACCOUNT_LEGACY_SESSION_MIGRATED", phone=mask_phone(account["phone"]))
        return await self._status_for(self._repo.get(account["id"]))

    async def start_login(self, account_id: Optional[str] = None, force_sql: bool = False) -> Dict:
        if self._repo is None or self._login_repo is None:
            raise RuntimeError("SQL login repository chưa sẵn sàng.")
        account = self._repo.get(account_id) if account_id else self._repo.get_default()
        if account is None:
            raise ValueError("Không tìm thấy tài khoản Telegram.")
        if self._account_in_use(account["phone"]):
            raise AccountAlreadyBusy("Tài khoản đang được một job sử dụng.")
        if self.active_login(account["id"]):
            raise AccountAlreadyBusy("Tài khoản này đang trong quá trình đăng nhập.")
        session_id = uuid.uuid4().hex[:12]
        service = self._new_service(account["phone"], force_sql=force_sql)
        try:
            result = await service.request_login_code()
            state = LoginState.COMPLETED.value if result["authorized"] else LoginState.WAIT_CODE.value
            row = self._login_repo.create(
                session_id,
                account["id"],
                state,
                result.get("phone_code_hash"),
            )
            if result["authorized"]:
                self._repo.mark_logged_in(account["id"])
                if self._session_backend(account["phone"]) == "SQL":
                    self._remove_legacy_session_files(account["phone"])
                self._status_cache[account["id"]] = (time.monotonic(), True)
            await self._sse.publish({"type": "account_login", **self._public_login(row)})
            return self._public_login(row)
        except Exception as exc:
            message = _friendly_login_error(exc)
            row = self._login_repo.create(session_id, account["id"], LoginState.FAILED.value, None)
            self._login_repo.update(session_id, LoginState.FAILED.value, message)
            log_event(logger, "ACCOUNT_LOGIN_FAILED", phone=mask_phone(account["phone"]), reason=message)
            await self._sse.publish({"type": "account_login", **self._public_login(self._login_repo.get(session_id))})
            return self._public_login(self._login_repo.get(session_id))

    async def submit_code(self, session_id: str, code: str, password: Optional[str] = None) -> Dict:
        if self._login_repo is None or self._repo is None:
            raise LoginSessionNotFound("Không tìm thấy phiên đăng nhập.")
        row = self._login_repo.get(session_id)
        if row is None:
            raise LoginSessionNotFound("Không tìm thấy phiên đăng nhập.")
        account = self._repo.get(row["account_id"])
        if account is None:
            raise LoginSessionNotFound("Tài khoản của phiên đăng nhập không còn tồn tại.")
        service = self._new_service(account["phone"])
        try:
            if row["state"] == LoginState.WAIT_CODE.value:
                if not code.strip():
                    raise ValueError("Mã xác nhận không được để trống.")
                result = await service.sign_in_code(code.strip(), row.get("phone_code_hash") or "")
                if result.get("needs_password"):
                    self._login_repo.update(session_id, LoginState.WAIT_2FA.value, None)
                elif result.get("authorized"):
                    self._login_repo.update(session_id, LoginState.COMPLETED.value, None)
                    self._repo.mark_logged_in(account["id"])
                    if self._session_backend(account["phone"]) == "SQL":
                        self._remove_legacy_session_files(account["phone"])
                    self._status_cache[account["id"]] = (time.monotonic(), True)
            elif row["state"] == LoginState.WAIT_2FA.value:
                if not password:
                    raise ValueError("Đang chờ mật khẩu 2FA Telegram.")
                result = await service.sign_in_password(password)
                if result.get("authorized"):
                    self._login_repo.update(session_id, LoginState.COMPLETED.value, None)
                    self._repo.mark_logged_in(account["id"])
                    if self._session_backend(account["phone"]) == "SQL":
                        self._remove_legacy_session_files(account["phone"])
                    self._status_cache[account["id"]] = (time.monotonic(), True)
            updated = self._login_repo.get(session_id)
            await self._sse.publish({"type": "account_login", **self._public_login(updated)})
            return self._public_login(updated)
        except ValueError:
            raise
        except Exception as exc:
            message = _friendly_login_error(exc)
            self._login_repo.update(session_id, row["state"], message)
            updated = self._login_repo.get(session_id)
            await self._sse.publish({"type": "account_login", **self._public_login(updated)})
            return self._public_login(updated)

    async def cancel_login(self, session_id: str) -> None:
        if self._login_repo is None:
            raise LoginSessionNotFound("Không tìm thấy phiên đăng nhập.")
        row = self._login_repo.get(session_id)
        if row is None:
            raise LoginSessionNotFound("Không tìm thấy phiên đăng nhập.")
        self._login_repo.update(session_id, LoginState.FAILED.value, "Đăng nhập đã bị hủy.")

    def set_default(self, account_id: str) -> None:
        self._repo.set_default(account_id)

    async def logout(self, account_id: Optional[str] = None) -> Dict:
        account = self._repo.get(account_id) if account_id else self._repo.get_default()
        if account is None:
            raise ValueError("Không tìm thấy tài khoản Telegram.")
        if self._account_in_use(account["phone"]):
            raise AccountAlreadyBusy("Tài khoản đang được job sử dụng; không thể đăng xuất.")
        if self._job_repo and self._job_repo.has_unfinished_jobs_for_account(account["phone"]):
            raise AccountAlreadyBusy("Tài khoản còn job chưa kết thúc; hãy hoàn tất, hủy hoặc xóa job trước khi đăng xuất.")
        if self.active_login(account["id"]):
            raise AccountAlreadyBusy("Tài khoản đang trong quá trình đăng nhập.")
        with self._db.transaction():
            if self._session_store is not None:
                self._session_store.delete(account["phone"])
        self._remove_legacy_session_files(account["phone"])
        self._status_cache[account["id"]] = (time.monotonic(), False)
        return {"deleted": True, "account_id": account["id"]}

    async def remove_account(self, account_id: str) -> None:
        account = self._repo.get(account_id)
        if account is None:
            return
        if self._account_in_use(account["phone"]):
            raise AccountAlreadyBusy("Tài khoản đang được job sử dụng; không thể xóa.")
        if self.active_login(account_id):
            raise AccountAlreadyBusy("Hãy hủy phiên đăng nhập trước khi xóa tài khoản.")
        if self._job_repo and self._job_repo.has_unfinished_jobs_for_account(account["phone"]):
            raise AccountAlreadyBusy("Tài khoản còn job chưa kết thúc; không thể xóa.")
        with self._db.transaction():
            if self._session_store is not None:
                self._session_store.delete(account["phone"])
            self._repo.delete(account_id)
        self._remove_legacy_session_files(account["phone"])
        self._status_cache.pop(account_id, None)

    async def start_qr_login(self) -> dict:
        if not self._config.api_id or not self._config.api_hash:
            raise ValueError("Chưa cấu hình API_ID/API_HASH Telegram.")
        qr_id = uuid.uuid4().hex[:16]
        client = TelegramClient(
            StringSession(), int(self._config.api_id), self._config.api_hash,
            proxy=parse_proxy(self._config.proxy) if self._config.proxy else None,
        )
        await client.connect()
        try:
            qr = await client.qr_login()
        except Exception:
            await client.disconnect()
            raise
        entry = {
            "client": client, "qr": qr, "state": "WAIT_SCAN", "error": None,
            "account_id": None, "needs_2fa": False,
        }
        self._qr_pending[qr_id] = entry
        entry["task"] = asyncio.create_task(self._wait_qr(qr_id))
        return {
            "qr_id": qr_id, "url": qr.url, "state": "WAIT_SCAN",
            "expires_at": qr.expires.isoformat() if qr.expires else None,
        }

    async def _wait_qr(self, qr_id: str) -> None:
        entry = self._qr_pending.get(qr_id)
        if not entry:
            return
        try:
            await entry["qr"].wait()
            await self._finalize_qr(qr_id)
        except errors.SessionPasswordNeededError:
            entry["state"] = "WAIT_2FA"
            entry["needs_2fa"] = True
        except asyncio.TimeoutError:
            entry["state"] = "EXPIRED"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            entry["state"] = "FAILED"
            entry["error"] = _friendly_login_error(exc)

    async def _finalize_qr(self, qr_id: str) -> dict:
        entry = self._qr_pending.get(qr_id)
        if not entry:
            raise LoginSessionNotFound("Không tìm thấy phiên QR.")
        client = entry["client"]
        me = await client.get_me()
        raw_phone = getattr(me, "phone", None)
        if not raw_phone:
            raise ValueError("Telegram không trả về số điện thoại cho phiên QR.")
        phone = self._normalize_phone("+" + str(raw_phone).lstrip("+"))
        account = self._repo.get_by_phone(phone) if self._repo else None
        if account and (self._account_in_use(phone) or (self._job_repo and self._job_repo.has_unfinished_jobs_for_account(phone))):
            raise AccountAlreadyBusy("Tài khoản QR đang có job chưa kết thúc; không thể thay session.")
        if account is None:
            label = getattr(me, "username", None) or getattr(me, "first_name", None) or "Tài khoản QR"
            account = self._repo.create(uuid.uuid4().hex[:12], phone, label)
        session_string = StringSession.save(client.session)
        if not session_string:
            raise RuntimeError("Không thể tạo StringSession từ phiên QR.")
        self._session_store.save(phone, session_string)
        self._repo.mark_logged_in(account["id"])
        self._status_cache[account["id"]] = (time.monotonic(), True)
        entry["state"] = "COMPLETED"
        entry["account_id"] = account["id"]
        await client.disconnect()
        log_event(logger, "ACCOUNT_QR_LOGIN_COMPLETED", phone=mask_phone(phone))
        return await self._status_for(self._repo.get(account["id"]))

    async def qr_status(self, qr_id: str) -> dict:
        entry = self._qr_pending.get(qr_id)
        if not entry:
            raise LoginSessionNotFound("Không tìm thấy phiên QR.")
        result = {"qr_id": qr_id, "state": entry["state"], "error": entry.get("error")}
        if entry.get("account_id") and self._repo:
            result["account"] = await self._status_for(self._repo.get(entry["account_id"]))
        return result

    async def refresh_qr(self, qr_id: str) -> dict:
        entry = self._qr_pending.get(qr_id)
        if not entry:
            raise LoginSessionNotFound("Không tìm thấy phiên QR.")
        if entry["state"] == "COMPLETED":
            return await self.qr_status(qr_id)
        old = entry.get("task")
        if old and not old.done():
            old.cancel()
            try:
                await old
            except asyncio.CancelledError:
                pass
        qr = await entry["client"].qr_login()
        entry.update({"qr": qr, "state": "WAIT_SCAN", "error": None, "needs_2fa": False})
        entry["task"] = asyncio.create_task(self._wait_qr(qr_id))
        return {"qr_id": qr_id, "url": qr.url, "state": "WAIT_SCAN", "expires_at": qr.expires.isoformat() if qr.expires else None}

    async def submit_qr_2fa(self, qr_id: str, password: str) -> dict:
        entry = self._qr_pending.get(qr_id)
        if not entry:
            raise LoginSessionNotFound("Không tìm thấy phiên QR.")
        if entry["state"] != "WAIT_2FA":
            raise ValueError("Phiên QR hiện không chờ mật khẩu 2FA.")
        await entry["client"].sign_in(password=password)
        return await self._finalize_qr(qr_id)

    async def cancel_qr(self, qr_id: str) -> None:
        entry = self._qr_pending.pop(qr_id, None)
        if not entry:
            return
        task = entry.get("task")
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        try:
            await entry["client"].disconnect()
        except Exception:
            pass

    async def shutdown_async(self) -> None:
        for qr_id in list(self._qr_pending):
            await self.cancel_qr(qr_id)

    def shutdown(self) -> None:
        # Durable login/session state is SQL-backed. QR waits are transient and
        # are cleaned by shutdown_async() from the FastAPI lifespan.
        return None
