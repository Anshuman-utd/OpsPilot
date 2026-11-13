"""Manage accounts from a trusted host using `python -m app.accounts`."""

import argparse
import asyncio
from getpass import getpass

from sqlalchemy import delete

from app.core.auth import create_invite, normalize_username, password_hash
from app.core.database import close_db_engine, get_session_factory
from app.models.account import Account, AccountSession


async def save_account(username: str, role: str, password: str | None) -> None:
    try:
        async with get_session_factory()() as db:
            account = await db.get(Account, username, with_for_update=True)
            if password is None:
                if account is None:
                    raise SystemExit("Account does not exist.")
                account.enabled = False
            else:
                hashed = await asyncio.to_thread(password_hash, password)
                if account is None:
                    db.add(Account(username=username, password_hash=hashed, role=role, enabled=True))
                else:
                    account.password_hash = hashed
                    account.role = role
                    account.enabled = True
            await db.execute(delete(AccountSession).where(AccountSession.username == username))
            await db.commit()
    finally:
        await close_db_engine()


async def invite_account(role: str) -> None:
    try:
        async with get_session_factory()() as db:
            code, expires = await create_invite(db, role, "host-admin")
            print(f"One-time {role} signup code (expires {expires.isoformat()}): {code}")
    finally:
        await close_db_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username", nargs="?")
    parser.add_argument("--invite", action="store_true", help="Create a one-time signup code")
    parser.add_argument("--role", choices=("operator", "viewer"), default="operator")
    parser.add_argument("--disable", action="store_true")
    args = parser.parse_args()
    if args.invite:
        if args.username or args.disable:
            parser.error("--invite only accepts --role")
        asyncio.run(invite_account(args.role))
        return
    if args.username is None:
        parser.error("Provide a username or use --invite")
    try:
        username = normalize_username(args.username)
    except ValueError as exc:
        parser.error(str(exc))
    password = None
    if not args.disable:
        password = getpass("Password (12 characters minimum): ")
        if not 12 <= len(password) <= 256 or password != getpass("Confirm password: "):
            parser.error("Passwords must match and be between 12 and 256 characters.")
    asyncio.run(save_account(username, args.role, password))
    print(f"{'Disabled' if args.disable else 'Saved'} account {username}.")


if __name__ == "__main__":
    main()
