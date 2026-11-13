"""Session endpoints for browser clients."""

import asyncio
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    COOKIE_NAME,
    authorize_workspace,
    create_invite,
    normalize_username,
    password_hash,
    require_account,
    require_trusted_origin,
    session_hash,
    verify_password,
)
from app.core.config import get_settings
from app.core.database import get_db_session
from app.core.redis import get_redis_client
from app.models.account import Account, AccountInvite, AccountSession

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
    demo_lab_enabled: bool


@router.post("/login", response_model=Identity)
async def login(
    credentials: Credentials, request: Request, response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Identity:
    require_trusted_origin(request)
    settings = get_settings()
    if not settings.auth_required:
        raise HTTPException(status_code=409, detail="Account access is disabled in local development.")
    await limit_auth_attempts(request)
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

    return await issue_session(account, response, db)


async def limit_auth_attempts(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    bucket = int(datetime.now(UTC).timestamp()) // 300
    key = f"opspilot:auth:{bucket}:{session_hash(client_ip)}"
    try:
        async with get_redis_client().pipeline(transaction=True) as pipe:
            pipe.incr(key).expire(key, 300)
            attempts, _ = await pipe.execute()
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Sign-in is temporarily unavailable.") from exc
    if attempts > 12:
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again shortly.")


class Signup(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=12, max_length=256)
    invite_code: str = Field(min_length=43, max_length=43)


class Invite(BaseModel):
    code: str
    expires_at: datetime


@router.post("/invites", response_model=Invite, status_code=201)
async def invite(
    account: Annotated[Account, Depends(authorize_workspace)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Invite:
    if not get_settings().auth_required:
        raise HTTPException(status_code=409, detail="Enable workspace accounts to invite teammates.")
    code, expires_at = await create_invite(db, "viewer", account.username)
    return Invite(code=code, expires_at=expires_at)


@router.post("/signup", response_model=Identity, status_code=201)
async def signup(
    payload: Signup, request: Request, response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Identity:
    require_trusted_origin(request)
    if not get_settings().auth_required:
        raise HTTPException(status_code=409, detail="Workspace accounts are disabled.")
    await limit_auth_attempts(request)
    try:
        username = normalize_username(payload.username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    invite_hash = session_hash(payload.invite_code)
    if not await db.scalar(select(AccountInvite.token_hash).where(
        AccountInvite.token_hash == invite_hash, AccountInvite.expires_at > func.now()
    )):
        raise HTTPException(status_code=400, detail="This invite is invalid, expired, or already used.")
    if _login_slots.locked():
        raise HTTPException(status_code=429, detail="Account service is busy. Try again shortly.")
    async with _login_slots:
        hashed = await asyncio.to_thread(password_hash, payload.password)

    role = await db.scalar(delete(AccountInvite).where(
        AccountInvite.token_hash == invite_hash, AccountInvite.expires_at > func.now()
    ).returning(AccountInvite.role))
    if role is None:
        raise HTTPException(status_code=400, detail="This invite is invalid, expired, or already used.")
    account = Account(username=username, role=role, password_hash=hashed, enabled=True)
    db.add(account)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="That username is already in use.") from exc
    return await issue_session(account, response, db)


async def issue_session(account: Account, response: Response, db: AsyncSession) -> Identity:
    settings = get_settings()
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
        demo_lab_enabled=settings.demo_lab_enabled,
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
