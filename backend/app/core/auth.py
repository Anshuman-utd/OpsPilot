"""Password hashing and authorization for the OpsPilot workspace."""

from hashlib import scrypt, sha256
from secrets import compare_digest, token_bytes
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db_session
from app.models.account import Account, AccountSession

COOKIE_NAME = "opspilot_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else token_bytes(16)
    digest = scrypt(password.encode(), salt=salt, n=2**17, r=8, p=1, dklen=32, maxmem=256 * 1024**2)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    salt = bytes.fromhex(stored.split(":", 1)[0]) if stored else bytes(16)
    candidate = password_hash(password, salt)
    return stored is not None and compare_digest(candidate, stored)


def session_hash(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def require_trusted_origin(request: Request) -> None:
    allowed = get_settings().PUBLIC_ORIGIN.rstrip("/")
    if request.headers.get("origin") != allowed:
        raise HTTPException(status_code=403, detail="Request origin is not allowed.")


async def require_account(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Account:
    settings = get_settings()
    if not settings.auth_required:
        return Account(username="local-operator", role="operator", enabled=True)
    if request.method not in SAFE_METHODS:
        require_trusted_origin(request)
    token = request.cookies.get(COOKIE_NAME)
    if token and len(token) == 43:
        account = await db.scalar(
            select(Account)
            .join(AccountSession, AccountSession.username == Account.username)
            .where(
                AccountSession.token_hash == session_hash(token),
                AccountSession.expires_at > func.now(),
                Account.enabled.is_(True),
            )
        )
        if account:
            return account
    raise HTTPException(status_code=401, detail="Sign in to OpsPilot.")


async def authorize_workspace(
    request: Request,
    account: Annotated[Account, Depends(require_account)],
) -> Account:
    if request.method not in SAFE_METHODS and account.role != "operator":
        raise HTTPException(status_code=403, detail="Operator access is required.")
    return account
