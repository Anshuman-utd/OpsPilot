from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import password_hash
from app.core.config import get_settings
from app.core.database import get_db_session
from app.main import create_application
from app.models.account import Account

ORIGIN = "https://workspace.example"
PASSWORD = "test password for workspace"


@pytest.mark.asyncio
async def test_workspace_session_and_roles(monkeypatch, sqlite_session):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_ORIGIN", ORIGIN)
    get_settings.cache_clear()
    try:
        for name, role in (("alice", "operator"), ("bob", "viewer")):
            sqlite_session.add(Account(
                username=name, role=role, enabled=True, password_hash=password_hash(PASSWORD)
            ))
        await sqlite_session.commit()

        api = create_application()

        async def database():
            yield sqlite_session

        api.dependency_overrides[get_db_session] = database
        pipe = MagicMock()
        pipe.incr.return_value = pipe
        pipe.execute = AsyncMock(return_value=[1, True])
        redis = MagicMock()
        redis.pipeline.return_value.__aenter__.return_value = pipe
        monkeypatch.setattr("app.api.auth.get_redis_client", lambda: redis)

        async with AsyncClient(transport=ASGITransport(app=api), base_url=ORIGIN) as client:
            assert (await client.get("/api/v1/incidents")).status_code == 401
            assert (await client.get("/docs")).status_code == 404

            viewer_login = await client.post("/api/auth/login", headers={"Origin": ORIGIN}, json={
                "username": "bob", "password": PASSWORD,
            })
            assert viewer_login.json()["role"] == "viewer"
            cookie = viewer_login.headers["set-cookie"].lower()
            assert all(attribute in cookie for attribute in ["httponly", "secure", "samesite=lax"])
            assert (await client.get("/api/v1/incidents")).status_code == 200
            assert (await client.post("/api/v1/incidents/evaluate", headers={"Origin": ORIGIN})).status_code == 403
            assert (await client.post("/api/auth/logout", headers={"Origin": "https://other.example"})).status_code == 403
            old_cookie = client.cookies.get("opspilot_session")
            assert (await client.post("/api/auth/logout", headers={"Origin": ORIGIN})).status_code == 204
            assert (await client.get("/api/auth/session", headers={
                "Cookie": f"opspilot_session={old_cookie}"
            })).status_code == 401

            assert (await client.post("/api/auth/login", headers={"Origin": ORIGIN}, json={
                "username": "alice", "password": PASSWORD,
            })).json()["role"] == "operator"
            # An operator reaches the resource handler.
            response = await client.post("/api/v1/incidents/missing/acknowledge", headers={"Origin": ORIGIN})
            assert response.status_code == 404
            invite = await client.post("/api/auth/invites", headers={"Origin": ORIGIN})
            assert invite.status_code == 201
            signup = {"username": "new-viewer", "password": PASSWORD, "invite_code": invite.json()["code"]}
            assert (await client.post("/api/auth/signup", headers={"Origin": "https://other.example"}, json=signup)).status_code == 403
            duplicate = {**signup, "username": "bob"}
            assert (await client.post("/api/auth/signup", headers={"Origin": ORIGIN}, json=duplicate)).status_code == 409
            registered = await client.post("/api/auth/signup", headers={"Origin": ORIGIN}, json=signup)
            assert registered.status_code == 201
            assert registered.json()["role"] == "viewer"
            assert (await client.post("/api/auth/signup", headers={"Origin": ORIGIN}, json=signup)).status_code == 400
            assert (await client.post("/api/auth/invites", headers={"Origin": ORIGIN})).status_code == 403
    finally:
        get_settings.cache_clear()
