"""Telegram multi-account management for the web UI."""

import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from ..account_manager import AccountAlreadyBusy, LoginSessionNotFound
from ...session_importer import SessionImportError
from ..deps import get_context, require_user

router = APIRouter(
    prefix="/api/account", tags=["account"], dependencies=[Depends(require_user)]
)


class CodeBody(BaseModel):
    session_id: str
    code: Optional[str] = None
    password: Optional[str] = None


class QrTwoFaBody(BaseModel):
    password: str


class AddAccountBody(BaseModel):
    phone: str
    label: Optional[str] = None



TELETHON_FILE_MAX_BYTES = 10 * 1024 * 1024


@router.post("/session/import")
async def import_session(
    request: Request,
    file: UploadFile = File(...),
    label: Optional[str] = Form(None),
    account_id: Optional[str] = Form(None),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix != ".session":
        raise HTTPException(status_code=415, detail="Chỉ hỗ trợ file Telethon .session.")
    fd, temp_name = tempfile.mkstemp(prefix="tg-session-", suffix=".session")
    os.chmod(temp_name, 0o600)
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > TELETHON_FILE_MAX_BYTES:
                    raise HTTPException(status_code=413, detail="File session vượt quá 10 MB.")
                out.write(chunk)
        if size < 100:
            raise HTTPException(status_code=400, detail="File session rỗng hoặc không hợp lệ.")
        try:
            result = await get_context(request).account.import_session_file(
                Path(temp_name), label=label, account_id=account_id
            )
            return {"ok": True, "account": result}
        except AccountAlreadyBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except (SessionImportError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    finally:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except OSError:
            pass

@router.post("/qr/start")
async def qr_start(request: Request) -> dict:
    try:
        return await get_context(request).account.start_qr_login()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/qr/{qr_id}")
async def qr_status(request: Request, qr_id: str) -> dict:
    try:
        return await get_context(request).account.qr_status(qr_id)
    except LoginSessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/qr/{qr_id}/refresh")
async def qr_refresh(request: Request, qr_id: str) -> dict:
    try:
        return await get_context(request).account.refresh_qr(qr_id)
    except LoginSessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/qr/{qr_id}/2fa")
async def qr_2fa(request: Request, qr_id: str, body: QrTwoFaBody) -> dict:
    try:
        return await get_context(request).account.submit_qr_2fa(qr_id, body.password)
    except LoginSessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except AccountAlreadyBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/qr/{qr_id}/cancel")
async def qr_cancel(request: Request, qr_id: str) -> dict:
    await get_context(request).account.cancel_qr(qr_id)
    return {"ok": True}


@router.get("/accounts")
async def accounts(request: Request) -> dict:
    return await get_context(request).account.list_accounts()


@router.post("/accounts")
async def add_account(request: Request, body: AddAccountBody) -> dict:
    ctx = get_context(request)
    try:
        account = ctx.account.add_account(body.phone, body.label)
        return {"id": account["id"], "ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/accounts/{account_id}/login/start")
async def account_login_start(request: Request, account_id: str) -> dict:
    ctx = get_context(request)
    try:
        return await ctx.account.start_login(account_id)
    except AccountAlreadyBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/accounts/{account_id}/session/migrate")
async def migrate_legacy_session(request: Request, account_id: str) -> dict:
    ctx = get_context(request)
    try:
        return await ctx.account.migrate_legacy_session(account_id)
    except AccountAlreadyBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (SessionImportError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/accounts/{account_id}/login/sql/start")
async def account_sql_login_start(request: Request, account_id: str) -> dict:
    ctx = get_context(request)
    try:
        return await ctx.account.start_login(account_id, force_sql=True)
    except AccountAlreadyBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/accounts/{account_id}/default")
async def set_default(request: Request, account_id: str) -> dict:
    try:
        get_context(request).account.set_default(account_id)
        return {"ok": True, "account_id": account_id}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/accounts/{account_id}/logout")
async def logout_account(request: Request, account_id: str) -> dict:
    try:
        return await get_context(request).account.logout(account_id)
    except AccountAlreadyBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

@router.delete("/accounts/{account_id}")
async def delete_account(request: Request, account_id: str) -> dict:
    try:
        await get_context(request).account.remove_account(account_id)
        return {"ok": True, "account_id": account_id}
    except AccountAlreadyBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/status")
async def account_status(request: Request) -> dict:
    return await get_context(request).account.status()


@router.post("/login/start")
async def login_start(request: Request) -> dict:
    try:
        return await get_context(request).account.start_login()
    except AccountAlreadyBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/login/code")
async def login_code(request: Request, body: CodeBody) -> dict:
    try:
        return await get_context(request).account.submit_code(
            body.session_id, body.code or "", body.password
        )
    except LoginSessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/login/cancel")
async def login_cancel(request: Request, body: CodeBody) -> dict:
    try:
        await get_context(request).account.cancel_login(body.session_id)
        return {"ok": True}
    except LoginSessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/logout")
async def account_logout(request: Request) -> dict:
    try:
        return await get_context(request).account.logout()
    except AccountAlreadyBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
