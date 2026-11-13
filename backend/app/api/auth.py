"""Session endpoints for browser clients."""

import asyncio
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    COOKIE_NAME,
    require_account,
    require_trusted_origin,
    session_hash,
    verify_password,
)
from app.core.config import get_settings
from app.core.database import get_db_session
from app.core.redis import get_redis_client
from app.models.account import Account, AccountSession

router = APIRouter(prefix="/api/auth", tags=["Accounts"])
_login_slots = asyncio.Semaphore(2)


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class Identity(BaseModel):
    username: str
    role: str
    authenticated: bool
    remediation_enabled: bool
    demo_orders_enabled: bool


@router.post("/login", response_model=Identity)
async def login(
    credentials: Credentials, request: Request, response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Identity:
    require_trusted_origin(request)
    settings = get_settings()
    if not settings.auth_required:
        raise HTTPException(status_code=409, detail="Account access is disabled in local development.")
    client_ip = request.client.host if request.client else "unknown"
    bucket = int(datetime.now(UTC).timestamp()) // 300
    key = f"opspilot:login:{bucket}:{session_hash(client_ip)}"
    try:
        async with get_redis_client().pipeline(transaction=True) as pipe:
            pipe.incr(key).expire(key, 300)
            attempts, _ = await pipe.execute()
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Sign-in is temporarily unavailable.") from exc
    if attempts > 12:
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again shortly.")
    username = credentials.username.strip().lower()
    stored = await db.scalar(select(Account.password_hash).where(Account.username == username))
    if _login_slots.locked():
        raise HTTPException(status_code=429, detail="Sign-in is busy. Try again shortly.")
    async with _login_slots:
        valid = await asyncio.to_thread(verify_password, credentials.password, stored)
    if not valid:
        raise HTTPException(status_code=401, detail="Incorrect username or password.")
    account = await db.get(Account, username, with_for_update=True, populate_existing=True)
    if account is None or not account.enabled or account.password_hash != stored:
        raise HTTPException(status_code=401, detail="Incorrect username or password.")

    token = token_urlsafe(32)
    duration = timedelta(hours=settings.SESSION_HOURS)
    now = datetime.now(UTC)
    await db.execute(delete(AccountSession).where(AccountSession.expires_at <= now))
    db.add(AccountSession(token_hash=session_hash(token), username=account.username, expires_at=now + duration))
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        COOKIE_NAME, token, httponly=True, secure=settings.secure_session_cookie,
        samesite="lax", max_age=int(duration.total_seconds()),
    )
    return identity_for(account)


@router.get("/session", response_model=Identity)
async def session(
    response: Response, account: Annotated[Account, Depends(require_account)],
) -> Identity:
    response.headers["Cache-Control"] = "no-store"
    return identity_for(account)


def identity_for(account: Account) -> Identity:
    settings = get_settings()
    return Identity(
        username=account.username,
        role=account.role,
        authenticated=settings.auth_required,
        remediation_enabled=settings.REMEDIATION_ENABLED,
        demo_orders_enabled=settings.DEMO_ORDERS_ENABLED,
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    require_trusted_origin(request)
    token = request.cookies.get(COOKIE_NAME)
    if token:
        await db.execute(delete(AccountSession).where(AccountSession.token_hash == session_hash(token)))
        await db.commit()
    response.delete_cookie(COOKIE_NAME, secure=get_settings().secure_session_cookie, httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
