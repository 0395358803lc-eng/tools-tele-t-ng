import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl import functions, types

from .models import CheckResponse, CheckStatus, ErrorType

logger = logging.getLogger(__name__)


class ProxyConfigError(ValueError):
    pass


def get_human_readable_user_status(status: types.TypeUserStatus):
    match status:
        case types.UserStatusOnline():
            return "Currently online"
        case types.UserStatusOffline():
            return status.was_online.strftime("%Y-%m-%d %H:%M:%S %Z")
        case types.UserStatusRecently():
            return "Last seen recently"
        case types.UserStatusLastWeek():
            return "Last seen last week"
        case types.UserStatusLastMonth():
            return "Last seen last month"
        case _:
            return "Unknown"


def parse_proxy(proxy_url: str):
    """Parse a proxy URL (e.g. 'socks5://user:pass@host:1080') into the tuple
    Telethon/PySocks expects. Requires the optional PySocks dependency.
    """
    from urllib.parse import urlsplit

    try:
        import socks
    except ImportError as e:
        raise ProxyConfigError(
            "Proxy support requires PySocks. Install it with: "
            "pip install telegram-phone-number-checker[proxy]"
        ) from e

    parsed = urlsplit(proxy_url)
    scheme_to_type = {
        "socks5": socks.SOCKS5,
        "socks4": socks.SOCKS4,
        "http": socks.HTTP,
    }
    if parsed.scheme not in scheme_to_type:
        raise ProxyConfigError(
            f"Unsupported proxy scheme '{parsed.scheme}'. Use socks5://, socks4://, or http://."
        )
    if not parsed.hostname or not parsed.port:
        raise ProxyConfigError(
            "Proxy URL must include a host and port, e.g. socks5://host:1080"
        )
    return (
        scheme_to_type[parsed.scheme],
        parsed.hostname,
        parsed.port,
        True,
        parsed.username,
        parsed.password,
    )


class TelegramService:
    """Thin transport + response classifier around the Telegram core flow.

    Responsibilities: connect, send request, classify the raw Telegram
    response into a structured CheckResponse. It NEVER decides retries or
    pacing — that is the JobManager's job.
    """

    def __init__(
        self,
        api_id: str,
        api_hash: str,
        phone_number: str,
        proxy: Optional[str] = None,
        session_dir: str = ".",
        session_loader=None,
        session_saver=None,
        management_rate_limiter=None,
        management_lock=None,
        management_claim=None,
        management_release=None,
    ):
        self._api_id = api_id
        self._api_hash = api_hash
        self._phone_number = phone_number
        self._proxy = parse_proxy(proxy) if proxy else None
        self._client: Optional[TelegramClient] = None
        self._session_dir = session_dir
        self._session_loader = session_loader
        self._session_saver = session_saver
        self._management_rate_limiter = management_rate_limiter
        self._management_lock = management_lock
        self._management_claim = management_claim
        self._management_release = management_release

    def _session_base_path(self) -> str:
        return str(Path(self._session_dir) / self._phone_number)


    def _new_client(self) -> TelegramClient:
        if self._session_loader is not None:
            stored = self._session_loader() or ""
            session = StringSession(stored)
        else:
            session = self._session_base_path()
        return TelegramClient(session, int(self._api_id), self._api_hash, proxy=self._proxy)

    def _persist_session(self, client: TelegramClient) -> None:
        if self._session_saver is not None:
            value = StringSession.save(client.session)
            if value:
                self._session_saver(value)
        else:
            self._harden_session_permissions()

    def _harden_session_permissions(self) -> None:
        base = self._session_base_path()
        for candidate in (Path(f"{base}.session"), Path(f"{base}.session-journal")):
            try:
                if candidate.exists():
                    candidate.chmod(0o600)
            except OSError as exc:
                logger.warning("Unable to harden Telegram session permissions: %s", exc)

    async def connect(
        self,
        code_callback=None,
        password_callback=None,
    ) -> None:
        """Connect and (if needed) log in.

        ``code_callback(phone)`` and ``password_callback(phone)`` are awaited
        to obtain the login code / 2FA password. When omitted the original
        blocking behaviour is used (``input()`` / ``getpass()``), so the CLI
        flow is unchanged while web/UI flows can supply a non-blocking source.
        """
        from getpass import getpass

        client = self._new_client()
        await client.connect()
        self._persist_session(client)
        if not await client.is_user_authorized():
            await client.send_code_request(self._phone_number)
            try:
                if code_callback is not None:
                    code = await code_callback(self._phone_number)
                else:
                    code = input("Enter the code (sent on telegram): ")
                await client.sign_in(self._phone_number, code)
            except errors.SessionPasswordNeededError:
                if password_callback is not None:
                    pw = await password_callback(self._phone_number)
                else:
                    pw = getpass(
                        "Two-Step Verification enabled. Please enter your account password: "
                    )
                await client.sign_in(password=pw)
        self._persist_session(client)
        self._client = client
        logger.info("Telegram client connected")

    async def is_authorized(self) -> bool:
        """Check whether the saved session authorizes this phone.

        Connects a fresh temporary client and disconnects immediately. Never
        prompts for a code, so it is safe to call from the web backend.
        """
        client = self._new_client()
        try:
            await client.connect()
            self._persist_session(client)
            return await client.is_user_authorized()
        finally:
            self._persist_session(client)
            await client.disconnect()

    async def request_login_code(self) -> dict:
        client = self._new_client()
        try:
            await client.connect()
            self._persist_session(client)
            if await client.is_user_authorized():
                return {"authorized": True, "phone_code_hash": None}
            sent = await client.send_code_request(self._phone_number)
            self._persist_session(client)
            return {"authorized": False, "phone_code_hash": sent.phone_code_hash}
        finally:
            self._persist_session(client)
            await client.disconnect()

    async def sign_in_code(self, code: str, phone_code_hash: str) -> dict:
        client = self._new_client()
        try:
            await client.connect()
            try:
                await client.sign_in(phone=self._phone_number, code=code, phone_code_hash=phone_code_hash)
            except errors.SessionPasswordNeededError:
                self._persist_session(client)
                return {"authorized": False, "needs_password": True}
            self._persist_session(client)
            return {"authorized": await client.is_user_authorized(), "needs_password": False}
        finally:
            self._persist_session(client)
            await client.disconnect()

    async def sign_in_password(self, password: str) -> dict:
        client = self._new_client()
        try:
            await client.connect()
            await client.sign_in(password=password)
            self._persist_session(client)
            return {"authorized": await client.is_user_authorized()}
        finally:
            self._persist_session(client)
            await client.disconnect()

    async def disconnect(self) -> None:
        if self._client is not None:
            self._persist_session(self._client)
            await self._client.disconnect()
            self._client = None
            logger.info("Telegram client disconnected")

    async def _authorized_call(self, callback):
        """Run one serialized, rate-limited management operation."""
        if self._management_lock is None:
            return await self._authorized_call_inner(callback)
        async with self._management_lock:
            return await self._authorized_call_inner(callback)

    async def _authorized_call_inner(self, callback):
        limiter = self._management_rate_limiter
        if limiter is not None:
            await limiter.acquire()
        claimed = False
        client = None
        try:
            if self._management_claim is not None:
                self._management_claim()
                claimed = True
            client = self._new_client()
            await client.connect()
            if not await client.is_user_authorized():
                raise RuntimeError("Telegram session is not authorized")
            result = await callback(client)
            self._persist_session(client)
            if limiter is not None:
                limiter.register_success()
            return result
        except (errors.FloodWaitError, errors.SlowModeWaitError) as exc:
            if limiter is not None:
                limiter.register_rate_limit(int(getattr(exc, "seconds", 0) or 0))
            raise
        finally:
            if client is not None:
                self._persist_session(client)
                await client.disconnect()
            if claimed and self._management_release is not None:
                self._management_release()

    async def get_profile(self) -> dict:
        async def op(client):
            me = await client.get_me()
            full = await client(functions.users.GetFullUserRequest(me))
            about = getattr(getattr(full, "full_user", None), "about", None) or ""
            return {
                "id": getattr(me, "id", None),
                "phone": getattr(me, "phone", None),
                "first_name": getattr(me, "first_name", None) or "",
                "last_name": getattr(me, "last_name", None) or "",
                "username": getattr(me, "username", None) or "",
                "about": about,
                "premium": bool(getattr(me, "premium", False)),
                "verified": bool(getattr(me, "verified", False)),
            }
        return await self._authorized_call(op)

    async def update_profile(self, first_name: str, last_name: str = "", about: str = "") -> dict:
        async def op(client):
            await client(functions.account.UpdateProfileRequest(
                first_name=first_name.strip(), last_name=last_name.strip(), about=about.strip()
            ))
            me = await client.get_me()
            return {"first_name": me.first_name or "", "last_name": me.last_name or "", "username": me.username or ""}
        return await self._authorized_call(op)

    async def update_username(self, username: str) -> dict:
        async def op(client):
            value = username.strip().lstrip("@")
            user = await client(functions.account.UpdateUsernameRequest(username=value))
            return {"username": getattr(user, "username", None) or ""}
        return await self._authorized_call(op)

    async def get_authorizations(self) -> list[dict]:
        async def op(client):
            result = await client(functions.account.GetAuthorizationsRequest())
            rows = []
            for item in getattr(result, "authorizations", []) or []:
                rows.append({
                    "hash": str(getattr(item, "hash", 0)),
                    "current": bool(getattr(item, "current", False)),
                    "device_model": getattr(item, "device_model", "") or "",
                    "platform": getattr(item, "platform", "") or "",
                    "system_version": getattr(item, "system_version", "") or "",
                    "app_name": getattr(item, "app_name", "") or "",
                    "app_version": getattr(item, "app_version", "") or "",
                    "ip": getattr(item, "ip", "") or "",
                    "country": getattr(item, "country", "") or "",
                    "region": getattr(item, "region", "") or "",
                    "date_active": getattr(item, "date_active", None).isoformat() if getattr(item, "date_active", None) else None,
                })
            return rows
        return await self._authorized_call(op)

    async def terminate_authorization(self, hash_id: int) -> bool:
        async def op(client):
            return bool(await client(functions.account.ResetAuthorizationRequest(hash=hash_id)))
        return await self._authorized_call(op)

    async def security_messages(self, limit: int = 50) -> list[dict]:
        async def op(client):
            messages = await client.get_messages(777000, limit=max(1, min(limit, 100)))
            return [{
                "id": getattr(m, "id", None),
                "text": getattr(m, "message", "") or "",
                "date": getattr(m, "date", None).isoformat() if getattr(m, "date", None) else None,
            } for m in messages]
        return await self._authorized_call(op)

    async def list_dialogs(self, limit: int = 100) -> list[dict]:
        async def op(client):
            dialogs = await client.get_dialogs(limit=max(1, min(limit, 200)))
            rows = []
            for d in dialogs:
                entity = getattr(d, "entity", None)
                if not (getattr(d, "is_group", False) or getattr(d, "is_channel", False)):
                    continue
                rows.append({
                    "id": str(getattr(entity, "id", "")),
                    "title": getattr(d, "name", None) or getattr(entity, "title", None) or "",
                    "username": getattr(entity, "username", None) or "",
                    "is_group": bool(getattr(d, "is_group", False)),
                    "is_channel": bool(getattr(d, "is_channel", False)),
                    "unread_count": int(getattr(d, "unread_count", 0) or 0),
                })
            return rows
        return await self._authorized_call(op)

    @staticmethod
    def _invite_hash(target: str) -> str | None:
        value = target.strip()
        for marker in ("t.me/+", "telegram.me/+", "t.me/joinchat/", "telegram.me/joinchat/"):
            if marker in value:
                return value.split(marker, 1)[1].split("?", 1)[0].strip("/")
        return None

    async def join_chat(self, target: str) -> dict:
        async def op(client):
            invite = self._invite_hash(target)
            if invite:
                result = await client(functions.messages.ImportChatInviteRequest(invite))
                chats = getattr(result, "chats", []) or []
                entity = chats[0] if chats else None
            else:
                entity = await client.get_entity(target.strip())
                await client(functions.channels.JoinChannelRequest(entity))
            return {"id": str(getattr(entity, "id", "")), "title": getattr(entity, "title", "") or "", "username": getattr(entity, "username", "") or ""}
        return await self._authorized_call(op)

    async def leave_chat(self, target: str) -> bool:
        async def op(client):
            raw = target.strip()
            entity = await client.get_entity(int(raw) if raw.lstrip("-").isdigit() else raw)
            await client(functions.channels.LeaveChannelRequest(entity))
            return True
        return await self._authorized_call(op)

    async def send_text(self, target: str, text: str) -> dict:
        async def op(client):
            msg = await client.send_message(target.strip(), text)
            return {"id": getattr(msg, "id", None), "date": getattr(msg, "date", None).isoformat() if getattr(msg, "date", None) else None}
        return await self._authorized_call(op)

    async def check_username(self, username: str) -> dict:
        async def op(client):
            value = username.strip().lstrip("@")
            if not value:
                return {"available": False, "reason": "empty"}
            try:
                ok = await client(functions.account.CheckUsernameRequest(username=value))
                return {"available": bool(ok), "reason": None}
            except errors.UsernameInvalidError:
                return {"available": False, "reason": "invalid"}
            except errors.UsernameOccupiedError:
                return {"available": False, "reason": "occupied"}
        return await self._authorized_call(op)

    async def upload_profile_photo(self, file_path: str) -> bool:
        async def op(client):
            uploaded = await client.upload_file(file_path)
            await client(functions.photos.UploadProfilePhotoRequest(file=uploaded))
            return True
        return await self._authorized_call(op)

    async def download_profile_photo(self) -> bytes | None:
        async def op(client):
            me = await client.get_me()
            data = await client.download_profile_photo(me, file=bytes)
            return data if isinstance(data, (bytes, bytearray)) else None
        return await self._authorized_call(op)

    async def terminate_other_authorizations(self) -> dict:
        async def op(client):
            result = await client(functions.account.GetAuthorizationsRequest())
            terminated = failed = 0
            for item in getattr(result, "authorizations", []) or []:
                if getattr(item, "current", False):
                    continue
                try:
                    await client(functions.account.ResetAuthorizationRequest(hash=getattr(item, "hash", 0)))
                    terminated += 1
                except Exception:
                    failed += 1
            return {"terminated": terminated, "failed": failed}
        return await self._authorized_call(op)

    @staticmethod
    def _parse_chat_input(raw: str) -> tuple[str, str | None]:
        value = (raw or "").strip()
        if not value:
            raise ValueError("Thiếu username hoặc link Telegram.")
        if value.lower().startswith("tg://resolve"):
            query = parse_qs(urlparse(value).query)
            domain = (query.get("domain") or [""])[0]
            if not domain:
                raise ValueError("Link tg:// không hợp lệ.")
            return domain, (query.get("start") or [None])[0]
        body = re.sub(r"^https?://", "", value, flags=re.I)
        if body.lower().startswith(("t.me/", "telegram.me/")):
            rest = body.split("/", 1)[1]
            if rest.startswith("+") or rest.lower().startswith("joinchat/"):
                raise ValueError("Invite link phải được tham gia từ mục Nhóm & Kênh trước.")
            path, _, query = rest.partition("?")
            segment = path.split("/", 1)[0]
            if not segment:
                raise ValueError("Link Telegram không hợp lệ.")
            start = (parse_qs(query).get("start") or [None])[0] if query else None
            return segment, start
        return value.lstrip("@"), None

    @staticmethod
    def _coerce_peer(value: str):
        raw = (value or "").strip()
        return int(raw) if re.fullmatch(r"-?\d+", raw) else raw

    @staticmethod
    def _peer_info(entity) -> dict:
        title = getattr(entity, "title", None)
        if not title:
            title = f"{getattr(entity, 'first_name', '') or ''} {getattr(entity, 'last_name', '') or ''}".strip()
        username = getattr(entity, "username", None)
        return {
            "id": str(getattr(entity, "id", "")),
            "ref": username or str(getattr(entity, "id", "")),
            "title": title or username or str(getattr(entity, "id", "")),
            "username": username,
            "kind": "bot" if bool(getattr(entity, "bot", False)) else "channel" if bool(getattr(entity, "broadcast", False)) else "group" if isinstance(entity, (types.Channel, types.Chat)) else "user",
            "is_bot": bool(getattr(entity, "bot", False)),
        }

    @staticmethod
    def _message_info(message) -> dict:
        text = getattr(message, "message", None) or ""
        date = getattr(message, "date", None)
        media = getattr(message, "media", None)
        return {
            "id": getattr(message, "id", 0),
            "out": bool(getattr(message, "out", False)),
            "text": text,
            "media": type(media).__name__ if media is not None and not text else None,
            "date": date.isoformat() if date else None,
            "service": bool(getattr(message, "action", None)),
            "sender_id": str(getattr(message, "sender_id", "") or "") or None,
        }

    async def open_chat(self, raw: str, limit: int = 40) -> dict:
        async def op(client):
            peer_ref, start_param = self._parse_chat_input(raw)
            entity = await client.get_entity(self._coerce_peer(peer_ref))
            started = False
            if start_param and bool(getattr(entity, "bot", False)):
                try:
                    await client(functions.messages.StartBotRequest(bot=entity, peer=entity, start_param=start_param))
                except Exception:
                    await client.send_message(entity, f"/start {start_param}")
                started = True
            rows = []
            async for msg in client.iter_messages(entity, limit=max(1, min(int(limit), 100))):
                rows.append(self._message_info(msg))
            rows.reverse()
            return {"peer": self._peer_info(entity), "started": started, "start_param": start_param, "messages": rows}
        return await self._authorized_call(op)

    async def chat_history(self, peer: str, limit: int = 40) -> list[dict]:
        async def op(client):
            entity = await client.get_entity(self._coerce_peer(peer))
            rows = []
            async for msg in client.iter_messages(entity, limit=max(1, min(int(limit), 100))):
                rows.append(self._message_info(msg))
            rows.reverse()
            return rows
        return await self._authorized_call(op)

    async def chat_send(self, peer: str, text: str) -> dict:
        async def op(client):
            entity = await client.get_entity(self._coerce_peer(peer))
            sent = await client.send_message(entity, text.strip())
            return self._message_info(sent)
        return await self._authorized_call(op)

    async def target_usage(self, target: str) -> dict:
        async def op(client):
            peer_ref, _ = self._parse_chat_input(target)
            entity = await client.get_entity(self._coerce_peer(peer_ref))
            info = self._peer_info(entity)
            if isinstance(entity, types.User):
                present = False
                async for _ in client.iter_messages(entity, limit=1):
                    present = True
                    break
                return {"status": "present" if present else "absent", "detail": "Có lịch sử tin nhắn" if present else "Chưa có lịch sử tin nhắn", "peer": info}
            target_id = getattr(entity, "id", None)
            present = False
            async for dialog in client.iter_dialogs():
                if getattr(getattr(dialog, "entity", None), "id", None) == target_id:
                    present = True
                    break
            return {"status": "present" if present else "absent", "detail": "Đang có trong danh sách hội thoại" if present else "Chưa tham gia/mở", "peer": info}
        return await self._authorized_call(op)

    @staticmethod
    def _parse_post_link(link: str):
        body = re.sub(r"^https?://", "", (link or "").strip(), flags=re.I)
        if not body.lower().startswith("t.me/"):
            raise ValueError("Link bài viết Telegram không hợp lệ.")
        parts = body[5:].split("/")
        if len(parts) >= 3 and parts[0] == "c" and parts[1].isdigit() and parts[2].isdigit():
            return types.PeerChannel(int(parts[1])), int(parts[2])
        if len(parts) >= 2 and parts[1].isdigit() and parts[0]:
            return parts[0], int(parts[1])
        raise ValueError("Link bài viết Telegram không hợp lệ.")

    async def react_post(self, post_link: str, emoji: str, custom_emoji_id: int | None = None) -> dict:
        async def op(client):
            peer_ref, msg_id = self._parse_post_link(post_link)
            entity = await client.get_entity(peer_ref)
            reaction = types.ReactionCustomEmoji(document_id=int(custom_emoji_id)) if custom_emoji_id else types.ReactionEmoji(emoticon=emoji)
            await client(functions.messages.SendReactionRequest(peer=entity, msg_id=msg_id, reaction=[reaction]))
            return {"ok": True, "message_id": msg_id}
        return await self._authorized_call(op)

    async def view_post(self, post_link: str) -> dict:
        async def op(client):
            peer_ref, msg_id = self._parse_post_link(post_link)
            entity = await client.get_entity(peer_ref)
            result = await client(functions.messages.GetMessagesViewsRequest(peer=entity, id=[msg_id], increment=True))
            views = None
            rows = getattr(result, "views", []) or []
            if rows:
                views = getattr(rows[0], "views", None)
            return {"ok": True, "message_id": msg_id, "views": views}
        return await self._authorized_call(op)

    async def available_reactions(self) -> list[str]:
        async def op(client):
            result = await client(functions.messages.GetAvailableReactionsRequest(hash=0))
            out = []
            for item in getattr(result, "reactions", []) or []:
                if getattr(item, "inactive", False):
                    continue
                emoji = getattr(item, "reaction", None)
                if emoji:
                    out.append(emoji)
            return out
        return await self._authorized_call(op)

    async def check_phone(self, phone: str, client_id: int = 0) -> CheckResponse:
        if self._client is None:
            return CheckResponse(
                status=CheckStatus.TEMPORARY_ERROR,
                phone=phone,
                error_type=ErrorType.UNKNOWN.value,
                error_message="Telegram client is not connected",
            )
        return await self._do_check_phone(phone, client_id)

    async def _do_check_phone(self, phone: str, client_id: int) -> CheckResponse:
        contact = types.InputPhoneContact(
            client_id=client_id, phone=phone, first_name="", last_name=""
        )
        try:
            import_response = await self._client(
                functions.contacts.ImportContactsRequest([contact])
            )
        except errors.FloodWaitError as e:
            return CheckResponse(
                status=CheckStatus.RATE_LIMITED,
                phone=phone,
                retry_after_seconds=int(e.seconds),
                error_type=ErrorType.FLOOD_WAIT.value,
                error_message=f"FloodWait for {e.seconds}s",
            )
        except errors.RpcCallFailError as e:
            return CheckResponse(
                status=CheckStatus.TEMPORARY_ERROR,
                phone=phone,
                error_type=ErrorType.TELEGRAM_ERROR.value,
                error_message=f"Telegram RPC call failed: {e}",
            )
        except (TimeoutError, ConnectionError, OSError) as e:
            return CheckResponse(
                status=CheckStatus.TEMPORARY_ERROR,
                phone=phone,
                error_type=ErrorType.NETWORK_TIMEOUT.value,
                error_message=str(e),
            )
        except Exception as e:
            return CheckResponse(
                status=CheckStatus.TEMPORARY_ERROR,
                phone=phone,
                error_type=ErrorType.UNKNOWN.value,
                error_message=str(e),
            )

        return await self._classify_import_response(import_response, phone, client_id)

    async def _classify_import_response(
        self, import_response, phone: str, client_id: int
    ) -> CheckResponse:
        users = list(getattr(import_response, "users", None) or [])
        retry_ids = list(getattr(import_response, "retry_contacts", None) or [])

        # Telegram explicitly asks to retry these contacts.
        if client_id in retry_ids or phone in retry_ids:
            return CheckResponse(
                status=CheckStatus.RETRY_REQUIRED,
                phone=phone,
                error_type=ErrorType.TELEGRAM_ERROR.value,
                error_message="contact listed in retry_contacts",
            )

        if len(users) == 1:
            user = users[0]
            resp = CheckResponse(
                status=CheckStatus.FOUND,
                phone=phone,
                telegram_user_id=getattr(user, "id", None),
                username=getattr(user, "username", None),
                first_name=getattr(user, "first_name", None),
                last_name=getattr(user, "last_name", None),
                user_was_online=get_human_readable_user_status(
                    getattr(user, "status", None)
                ),
            )
            try:
                await self._delete_contact(user)
            except Exception as e:
                logger.warning("Contact cleanup failed but lookup succeeded: %s", e)
                resp.cleanup_error = f"DeleteContactsRequest failed: {e}"
            return resp

        if len(users) == 0:
            return CheckResponse(
                status=CheckStatus.NOT_DISCOVERABLE,
                phone=phone,
                error_message="No user returned and contact not in retry_contacts",
            )

        return CheckResponse(
            status=CheckStatus.PERMANENT_ERROR,
            phone=phone,
            error_type=ErrorType.UNKNOWN.value,
            error_message="Matched multiple Telegram accounts unexpectedly",
        )

    async def _delete_contact(self, user) -> None:
        """Core Bellingcat step: delete the contact to get richer user data.

        If this fails it must NOT destroy an already-FOUND lookup result.
        The caller keeps the FOUND status; only cleanup_error is recorded.
        """
        await self._client(
            functions.contacts.DeleteContactsRequest(id=[getattr(user, "id", None)])
        )
