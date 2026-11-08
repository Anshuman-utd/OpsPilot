from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db_session
from app.main import app
from app.models.incident import IncidentModel, IncidentStatus


@pytest.fixture
async def client_with_sqlite():
    """FastAPI test client backed by an in-memory SQLite database."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, session_factory

    app.dependency_overrides.clear()
    await engine.dispose()


class TestIncidentLifecycleTransitions:
    """Tests the state machine transitions and audit trail of incidents."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_progression(self, client_with_sqlite):
        client, session_factory = client_with_sqlite

        # Seed an OPEN incident directly
        now = datetime.now(UTC)
        incident_id = "test-inc-lifecycle-1"
        async with session_factory() as session:
            inc = IncidentModel(
                id=incident_id,
                title="Elevated HTTP 5xx error rate",
                service_name="payment-service",
                severity="HIGH",
                status=IncidentStatus.OPEN.value,
                incident_type="http_5xx_rate",
                source="detection_engine",
                fingerprint="test-fp-lifecycle-1",
                triggered_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(inc)
            await session.commit()

        # Step 1: Acknowledge (OPEN -> ACKNOWLEDGED)
        resp1 = await client.post(
            f"/api/v1/incidents/{incident_id}/acknowledge",
            json={"actor": "sre-alice", "comment": "Acknowledged and investigating queue"},
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["status"] == "ACKNOWLEDGED"
        assert data1["acknowledged_at"] is not None
        assert any(e["event_type"] == "INCIDENT_ACKNOWLEDGED" for e in data1["events"])

        # Step 2: Investigate (ACKNOWLEDGED -> INVESTIGATING)
        resp2 = await client.post(
            f"/api/v1/incidents/{incident_id}/investigate",
            json={"actor": "sre-alice", "comment": "Checking downstream payment gateway logs"},
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["status"] == "INVESTIGATING"
        assert any(e["event_type"] == "INCIDENT_INVESTIGATING" for e in data2["events"])

        # Step 3: Resolve (INVESTIGATING -> RESOLVED)
        resp3 = await client.post(
            f"/api/v1/incidents/{incident_id}/resolve",
            json={"actor": "sre-alice", "comment": "Upstream bank recovered. Traffic normal."},
        )
        assert resp3.status_code == 200
        data3 = resp3.json()
        assert data3["status"] == "RESOLVED"
        assert data3["resolved_at"] is not None
        assert any(e["event_type"] == "INCIDENT_RESOLVED" for e in data3["events"])

        # Step 4: Invalid Transition (Attempt to acknowledge a resolved incident)
        resp4 = await client.post(
            f"/api/v1/incidents/{incident_id}/acknowledge",
            json={"actor": "sre-bob"},
        )
        assert resp4.status_code == 400
        assert "Cannot acknowledge an already resolved incident" in resp4.json()["detail"]

    @pytest.mark.asyncio
    async def test_idempotent_duplicate_acknowledge(self, client_with_sqlite):
        client, session_factory = client_with_sqlite

        now = datetime.now(UTC)
        incident_id = "test-inc-idempotent-1"
        async with session_factory() as session:
            inc = IncidentModel(
                id=incident_id,
                title="Service unreachable",
                service_name="api-gateway",
                severity="CRITICAL",
                status=IncidentStatus.OPEN.value,
                incident_type="service_unavailable",
                fingerprint="test-fp-idempotent-1",
                triggered_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(inc)
            await session.commit()

        # First acknowledge
        r1 = await client.post(f"/api/v1/incidents/{incident_id}/acknowledge")
        assert r1.status_code == 200
        assert r1.json()["status"] == "ACKNOWLEDGED"

        # Duplicate acknowledge should succeed idempotently
        r2 = await client.post(f"/api/v1/incidents/{incident_id}/acknowledge")
        assert r2.status_code == 200
        assert r2.json()["status"] == "ACKNOWLEDGED"
