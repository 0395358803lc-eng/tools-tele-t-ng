"""Authentication endpoints: login / logout / current user."""

import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth import COOKIE_NAME, TOKEN_MAX_AGE_SECONDS
from ..deps import get_context, require_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(request: Request, response: Response, body: LoginBody) -> dict:
    ctx = get_context(request)
    client_key = request.client.host if request.client else "unknown"
    retry_after = ctx.auth.login_retry_after(client_key)
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Quá nhiều lần đăng nhập thất bại. Vui lòng thử lại sau.",
            headers={"Retry-After": str(retry_after)},
        )
    if not body.username or not ctx.auth.verify_credentials(
        body.username, body.password
    ):
        ctx.auth.record_login_failure(client_key)
        raise HTTPException(status_code=401, detail="Sai tên đăng nhập hoặc mật khẩu.")
    ctx.auth.clear_login_failures(client_key)
    token = ctx.auth.issue_token(body.username)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=ctx.auth.cookie_secure,
        samesite="lax",
        max_age=ctx.auth.session_max_age,
        path="/",
    )
    return {"ok": True, "username": body.username}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    ctx = get_context(request)
    token = request.cookies.get(COOKIE_NAME)
    if token:
        ctx.auth.revoke_token(token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def me(username: str = Depends(require_user)) -> dict:
    return {"username": username}
