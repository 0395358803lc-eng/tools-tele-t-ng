"""Unified Telegram management APIs backed by canonical PostgreSQL sessions."""

import csv
import io
import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from ..account_manager import AccountAlreadyBusy
from ..deps import get_context, require_user

router = APIRouter(prefix="/api/manager", tags=["manager"], dependencies=[Depends(require_user)])
logger = logging.getLogger(__name__)


class ProfileBody(BaseModel):
    first_name: str = Field(min_length=1, max_length=64)
    last_name: str = Field(default="", max_length=64)
    about: str = Field(default="", max_length=140)


class UsernameBody(BaseModel):
    username: str = Field(default="", max_length=64)


class TargetBody(BaseModel):
    target: str = Field(min_length=1, max_length=512)


class MessageBody(TargetBody):
    text: str = Field(min_length=1, max_length=4096)


class BulkTargetBody(BaseModel):
    account_ids: list[str] = Field(min_length=1, max_length=50)
    target: str = Field(min_length=1, max_length=512)


class BulkAccountsBody(BaseModel):
    account_ids: list[str] = Field(min_length=1, max_length=50)


class ChatOpenBody(BaseModel):
    input: str = Field(min_length=1, max_length=512)
    limit: int = Field(default=40, ge=1, le=100)


class ChatSendBody(BaseModel):
    peer: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1, max_length=4096)


class TargetCheckBody(BaseModel):
    target: str = Field(min_length=1, max_length=512)
    account_ids: list[str] = Field(default_factory=list)


class PostBody(BaseModel):
    post_link: str = Field(min_length=1, max_length=512)


class ReactBody(PostBody):
    emoji: str = Field(min_length=1, max_length=32)
    custom_emoji_id: int | None = None


def _service(request: Request, account_id: str):
    try:
        return get_context(request).account.management_service(account_id)
    except AccountAlreadyBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _audit(request: Request, action: str, account_id: str | None, status: str = "ok", detail: str | None = None) -> None:
    try:
        get_context(request).manager_store.audit(action, account_id, status, detail)
    except Exception as exc:
        # Audit failure must never turn a completed Telegram action into an HTTP 500,
        # but it must remain observable for operators.
        logger.warning(
            "Manager audit write failed action=%s account_id=%s error=%s",
            action,
            account_id,
            type(exc).__name__,
        )


def _safe_error(exc: Exception) -> HTTPException:
    name = type(exc).__name__
    if isinstance(exc, AccountAlreadyBusy):
        return HTTPException(status_code=409, detail=str(exc))
    if name in {"UsernameInvalidError", "UsernameOccupiedError", "UsernameNotModifiedError"}:
        return HTTPException(status_code=400, detail=f"Telegram từ chối username ({name}).")
    if name in {"ChannelPrivateError", "InviteHashExpiredError", "InviteHashInvalidError"}:
        return HTTPException(status_code=400, detail="Link/nhóm Telegram không hợp lệ hoặc không còn truy cập được.")
    if name in {"FloodWaitError", "SlowModeWaitError"}:
        seconds = int(getattr(exc, "seconds", 0) or 0)
        return HTTPException(status_code=429, detail=f"Telegram đang giới hạn tốc độ. Thử lại sau {seconds}s.")
    if isinstance(exc, RuntimeError) and "not authorized" in str(exc).lower():
        return HTTPException(status_code=409, detail="Session Telegram chưa được xác thực.")
    return HTTPException(status_code=502, detail=f"Telegram operation failed ({name}).")


@router.get("/profile/{account_id}")
async def profile(request: Request, account_id: str) -> dict:
    try:
        return await _service(request, account_id).get_profile()
    except HTTPException:
        raise
    except Exception as exc:
        raise _safe_error(exc) from exc


@router.put("/profile/{account_id}")
async def update_profile(request: Request, account_id: str, body: ProfileBody) -> dict:
    try:
        result = await _service(request, account_id).update_profile(body.first_name, body.last_name, body.about)
        _audit(request, "profile.update", account_id)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        _audit(request, "profile.update", account_id, "failed", type(exc).__name__)
        raise _safe_error(exc) from exc


@router.put("/profile/{account_id}/username")
async def update_username(request: Request, account_id: str, body: UsernameBody) -> dict:
    try:
        result = await _service(request, account_id).update_username(body.username)
        _audit(request, "profile.username", account_id)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        _audit(request, "profile.username", account_id, "failed", type(exc).__name__)
        raise _safe_error(exc) from exc


@router.get("/security/{account_id}/sessions")
async def sessions(request: Request, account_id: str) -> dict:
    try:
        return {"items": await _service(request, account_id).get_authorizations()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _safe_error(exc) from exc


@router.delete("/security/{account_id}/sessions/{hash_id}")
async def terminate_session(request: Request, account_id: str, hash_id: int) -> dict:
    try:
        result = await _service(request, account_id).terminate_authorization(hash_id)
        _audit(request, "security.session.terminate", account_id)
        return {"ok": result}
    except HTTPException:
        raise
    except Exception as exc:
        _audit(request, "security.session.terminate", account_id, "failed", type(exc).__name__)
        raise _safe_error(exc) from exc


@router.get("/security/{account_id}/messages")
async def security_messages(request: Request, account_id: str, limit: int = 50) -> dict:
    ctx = get_context(request)
    try:
        live = await _service(request, account_id).security_messages(limit)
        ctx.manager_store.upsert_security_messages(account_id, live)
        return {"items": ctx.manager_store.security_messages(account_id, limit), "cached": False}
    except HTTPException:
        raise
    except Exception as exc:
        cached = ctx.manager_store.security_messages(account_id, limit)
        if cached:
            return {"items": cached, "cached": True}
        raise _safe_error(exc) from exc


@router.get("/groups/{account_id}")
async def groups(request: Request, account_id: str, limit: int = 100) -> dict:
    try:
        return {"items": await _service(request, account_id).list_dialogs(limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _safe_error(exc) from exc


@router.post("/groups/{account_id}/join")
async def join_group(request: Request, account_id: str, body: TargetBody) -> dict:
    try:
        result = await _service(request, account_id).join_chat(body.target)
        _audit(request, "groups.join", account_id)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        _audit(request, "groups.join", account_id, "failed", type(exc).__name__)
        raise _safe_error(exc) from exc


@router.post("/groups/{account_id}/leave")
async def leave_group(request: Request, account_id: str, body: TargetBody) -> dict:
    try:
        result = await _service(request, account_id).leave_chat(body.target)
        _audit(request, "groups.leave", account_id)
        return {"ok": result}
    except HTTPException:
        raise
    except Exception as exc:
        _audit(request, "groups.leave", account_id, "failed", type(exc).__name__)
        raise _safe_error(exc) from exc


@router.post("/messages/{account_id}/send")
async def send_message(request: Request, account_id: str, body: MessageBody) -> dict:
    try:
        result = await _service(request, account_id).send_text(body.target, body.text)
        _audit(request, "messages.send", account_id)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        _audit(request, "messages.send", account_id, "failed", type(exc).__name__)
        raise _safe_error(exc) from exc


async def _bulk_account_action(request: Request, account_ids: list[str], action: str, target: str | None = None) -> dict:
    unique_ids = list(dict.fromkeys(account_ids))
    results = []
    for account_id in unique_ids:
        try:
            service = _service(request, account_id)
            if action == "join":
                value = await service.join_chat(target or "")
            elif action == "leave":
                value = {"ok": await service.leave_chat(target or "")}
            elif action == "terminate_others":
                value = await service.terminate_other_authorizations()
            else:
                raise ValueError("Unknown bulk action")
            _audit(request, f"bulk.{action}", account_id)
            results.append({"account_id": account_id, "status": "ok", "result": value})
        except HTTPException as exc:
            _audit(request, f"bulk.{action}", account_id, "failed", str(exc.status_code))
            results.append({"account_id": account_id, "status": "failed", "detail": exc.detail})
        except Exception as exc:
            _audit(request, f"bulk.{action}", account_id, "failed", type(exc).__name__)
            results.append({"account_id": account_id, "status": "failed", "detail": _safe_error(exc).detail})
    return {
        "total": len(results),
        "succeeded": sum(1 for r in results if r["status"] == "ok"),
        "failed": sum(1 for r in results if r["status"] != "ok"),
        "results": results,
    }


@router.post("/bulk/join")
async def bulk_join(request: Request, body: BulkTargetBody) -> dict:
    return await _bulk_account_action(request, body.account_ids, "join", body.target)


@router.post("/bulk/leave")
async def bulk_leave(request: Request, body: BulkTargetBody) -> dict:
    return await _bulk_account_action(request, body.account_ids, "leave", body.target)


@router.post("/bulk/terminate-others")
async def bulk_terminate_others(request: Request, body: BulkAccountsBody) -> dict:
    return await _bulk_account_action(request, body.account_ids, "terminate_others")


@router.get("/audit")
async def audit(
    request: Request, limit: int = 100, offset: int = 0,
    action: str | None = None, account_id: str | None = None, status: str | None = None,
) -> dict:
    store = get_context(request).manager_store
    return {
        "items": store.recent_audit(limit, offset, action, account_id, status),
        "total": store.count_audit(action, account_id, status),
        "limit": max(1, min(int(limit), 500)),
        "offset": max(0, int(offset)),
    }


@router.get("/audit/export")
async def export_audit(
    request: Request, max_rows: int = 10000,
    action: str | None = None, account_id: str | None = None, status: str | None = None,
) -> Response:
    store = get_context(request).manager_store
    limit = max(1, min(int(max_rows), 10000))
    rows: list[dict] = []
    offset = 0
    while len(rows) < limit:
        batch = store.recent_audit(min(500, limit - len(rows)), offset, action, account_id, status)
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["id", "action", "account_id", "status", "detail", "created_at"])
    writer.writeheader()
    writer.writerows(rows)
    _audit(request, "audit.export", None, detail=f"rows={len(rows)}")
    return Response(
        output.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=manager-audit.csv"},
    )


@router.get("/profile/{account_id}/username-check")
async def check_username(request: Request, account_id: str, username: str) -> dict:
    try:
        return await _service(request, account_id).check_username(username)
    except HTTPException:
        raise
    except Exception as exc:
        raise _safe_error(exc) from exc


@router.post("/profile/{account_id}/photo")
async def upload_profile_photo(request: Request, account_id: str, file: UploadFile = File(...)) -> dict:
    allowed = {".jpg", ".jpeg", ".png", ".webp"}
    suffix = Path(file.filename or "photo.jpg").suffix.lower()
    if suffix not in allowed:
        raise HTTPException(status_code=415, detail="Chỉ hỗ trợ ảnh JPG, PNG hoặc WebP.")
    data = await file.read(15 * 1024 * 1024 + 1)
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Ảnh đại diện vượt quá 15 MB.")
    if len(data) < 16:
        raise HTTPException(status_code=400, detail="Ảnh đại diện rỗng hoặc không hợp lệ.")
    fd, path = tempfile.mkstemp(prefix="tg-avatar-", suffix=suffix)
    os.chmod(path, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        result = await _service(request, account_id).upload_profile_photo(path)
        _audit(request, "profile.photo", account_id)
        return {"ok": bool(result)}
    except HTTPException:
        raise
    except Exception as exc:
        _audit(request, "profile.photo", account_id, "failed", type(exc).__name__)
        raise _safe_error(exc) from exc
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


@router.get("/profile/{account_id}/photo")
async def get_profile_photo(request: Request, account_id: str):
    try:
        data = await _service(request, account_id).download_profile_photo()
        if not data:
            return Response(status_code=204)
        return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})
    except HTTPException:
        raise
    except Exception as exc:
        raise _safe_error(exc) from exc


@router.post("/security/{account_id}/sessions/terminate-others")
async def terminate_other_sessions(request: Request, account_id: str) -> dict:
    try:
        result = await _service(request, account_id).terminate_other_authorizations()
        _audit(request, "security.sessions.terminate_others", account_id)
        return {"ok": result.get("failed", 0) == 0, **result}
    except HTTPException:
        raise
    except Exception as exc:
        _audit(request, "security.sessions.terminate_others", account_id, "failed", type(exc).__name__)
        raise _safe_error(exc) from exc


@router.post("/messages/{account_id}/open")
async def open_chat(request: Request, account_id: str, body: ChatOpenBody) -> dict:
    try:
        result = await _service(request, account_id).open_chat(body.input, body.limit)
        if result.get("started"):
            _audit(request, "messages.bot_start", account_id)
        return result
    except HTTPException:
        raise
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise _safe_error(exc) from exc


@router.get("/messages/{account_id}/history")
async def chat_history(request: Request, account_id: str, peer: str, limit: int = 40) -> dict:
    try:
        return {"messages": await _service(request, account_id).chat_history(peer, limit)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _safe_error(exc) from exc


@router.post("/messages/{account_id}/chat-send")
async def chat_send(request: Request, account_id: str, body: ChatSendBody) -> dict:
    try:
        message = await _service(request, account_id).chat_send(body.peer, body.text)
        _audit(request, "messages.chat_send", account_id)
        return {"ok": True, "message": message}
    except HTTPException:
        raise
    except Exception as exc:
        _audit(request, "messages.chat_send", account_id, "failed", type(exc).__name__)
        raise _safe_error(exc) from exc


@router.post("/target-check")
async def target_check(request: Request, body: TargetCheckBody) -> dict:
    ctx = get_context(request)
    listing = await ctx.account.list_accounts()
    allowed = set(body.account_ids) if body.account_ids else {a["id"] for a in listing["items"]}
    results = []
    peer = None
    for account in listing["items"]:
        if account["id"] not in allowed:
            continue
        if account["state"] not in {"AUTHORIZED"}:
            results.append({"id": account["id"], "status": "skipped", "detail": "Account chưa sẵn sàng."})
            continue
        try:
            checked = await ctx.account.management_service(account["id"]).target_usage(body.target)
            peer = peer or checked.get("peer")
            results.append({"id": account["id"], "label": account["label"], **checked})
        except AccountAlreadyBusy:
            results.append({"id": account["id"], "label": account["label"], "status": "skipped", "detail": "Account đang được job sử dụng."})
        except Exception as exc:
            results.append({"id": account["id"], "label": account["label"], "status": "failed", "detail": type(exc).__name__})
    return {"target": body.target, "peer": peer, "total": len(results), "present": [r for r in results if r["status"] == "present"], "absent": [r for r in results if r["status"] == "absent"], "skipped": [r for r in results if r["status"] == "skipped"], "failed": [r for r in results if r["status"] == "failed"], "results": results}


@router.get("/posts/{account_id}/reactions")
async def available_reactions(request: Request, account_id: str) -> dict:
    try:
        return {"items": await _service(request, account_id).available_reactions()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _safe_error(exc) from exc


@router.post("/posts/{account_id}/react")
async def react_post(request: Request, account_id: str, body: ReactBody) -> dict:
    try:
        result = await _service(request, account_id).react_post(body.post_link, body.emoji, body.custom_emoji_id)
        _audit(request, "posts.react", account_id)
        return result
    except HTTPException:
        raise
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        _audit(request, "posts.react", account_id, "failed", type(exc).__name__)
        raise _safe_error(exc) from exc


@router.post("/posts/{account_id}/view")
async def view_post(request: Request, account_id: str, body: PostBody) -> dict:
    try:
        result = await _service(request, account_id).view_post(body.post_link)
        _audit(request, "posts.view", account_id)
        return result
    except HTTPException:
        raise
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        _audit(request, "posts.view", account_id, "failed", type(exc).__name__)
        raise _safe_error(exc) from exc
