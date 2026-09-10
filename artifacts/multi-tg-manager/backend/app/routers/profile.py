import io
import os
import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from telethon.errors import FloodWaitError, UsernameInvalidError, UsernameOccupiedError
from telethon.tl.functions.account import (
    CheckUsernameRequest,
    UpdateProfileRequest,
    UpdateUsernameRequest,
)
from telethon.tl.functions.photos import (
    UploadProfilePhotoRequest,
)

from ..config import settings
from ..db import get_db
from ..models import Account
from ..schemas import AccountOut, ProfileUpdateIn, UsernameCheckOut, UsernameUpdateIn
from ..tg_manager import manager
from .accounts import _account_to_out

router = APIRouter(prefix="/api/accounts/{account_id}/profile", tags=["profile"])


def _client_or_404(account_id: int):
    cli = manager.get(account_id)
    if not cli:
        raise HTTPException(409, "Account is not connected")
    return cli


@router.put("", response_model=AccountOut)
async def update_profile(
    account_id: int, body: ProfileUpdateIn, db: AsyncSession = Depends(get_db)
):
    acc = await db.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    cli = _client_or_404(account_id)
    try:
        kw = {}
        if body.first_name is not None:
            kw["first_name"] = body.first_name
        if body.last_name is not None:
            kw["last_name"] = body.last_name
        if body.bio is not None:
            if len(body.bio) > 70:
                raise HTTPException(400, "Bio max 70 chars")
            kw["about"] = body.bio
        if kw:
            await cli(UpdateProfileRequest(**kw))
        if body.first_name is not None:
            acc.first_name = body.first_name
        if body.last_name is not None:
            acc.last_name = body.last_name
        if body.bio is not None:
            acc.bio = body.bio
        await db.commit()
        await db.refresh(acc)
    except FloodWaitError as e:
        raise HTTPException(429, f"FloodWait: wait {e.seconds}s")
    except Exception as e:
        raise HTTPException(400, f"Profile update failed: {e}")
    return await _account_to_out(acc, db)


@router.get("/check_username", response_model=UsernameCheckOut)
async def check_username(account_id: int, username: str):
    cli = _client_or_404(account_id)
    if not username:
        return UsernameCheckOut(available=False, reason="empty")
    try:
        ok = await cli(CheckUsernameRequest(username=username))
        return UsernameCheckOut(available=bool(ok))
    except UsernameInvalidError:
        return UsernameCheckOut(available=False, reason="invalid")
    except UsernameOccupiedError:
        return UsernameCheckOut(available=False, reason="occupied")
    except Exception as e:
        return UsernameCheckOut(available=False, reason=str(e)[:80])


@router.put("/username", response_model=AccountOut)
async def update_username(
    account_id: int, body: UsernameUpdateIn, db: AsyncSession = Depends(get_db)
):
    acc = await db.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    cli = _client_or_404(account_id)
    try:
        await cli(UpdateUsernameRequest(username=body.username))
        acc.username = body.username
        await db.commit()
        await db.refresh(acc)
    except UsernameOccupiedError:
        raise HTTPException(409, "Username already taken")
    except UsernameInvalidError:
        raise HTTPException(400, "Invalid username")
    except FloodWaitError as e:
        raise HTTPException(429, f"FloodWait: wait {e.seconds}s")
    except Exception as e:
        raise HTTPException(400, str(e))
    return await _account_to_out(acc, db)


@router.post("/photo", response_model=AccountOut)
async def upload_photo(
    account_id: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)
):
    acc = await db.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    cli = _client_or_404(account_id)
    data = await file.read(settings.MAX_IMAGE_UPLOAD_BYTES + 1)
    if len(data) > settings.MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(413, "Profile image is too large")
    # Telethon needs a file path or BinaryIO with a name
    suffix = os.path.splitext(file.filename or "photo.jpg")[1] or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        uploaded = await cli.upload_file(tmp_path)
        await cli(UploadProfilePhotoRequest(file=uploaded))
    except FloodWaitError as e:
        raise HTTPException(429, f"FloodWait: wait {e.seconds}s")
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    return await _account_to_out(acc, db)


@router.get("/photo")
async def get_photo(account_id: int):
    """Return the current profile photo as binary JPEG for efficient avatar use."""
    cli = _client_or_404(account_id)
    try:
        me = await cli.get_me()
        buf = io.BytesIO()
        downloaded = await cli.download_profile_photo(me, file=buf)
        if not downloaded:
            return Response(status_code=204)
        return Response(
            content=buf.getvalue(),
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=300"},
        )
    except Exception:
        return Response(status_code=204)
