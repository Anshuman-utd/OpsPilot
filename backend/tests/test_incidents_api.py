from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db_session
from app.main import app
from app.models.incident import IncidentModel, IncidentSeverity, IncidentStatus
from app.schemas.detection import DetectionRunResponse


@pytest.fixture
async def client_with_sqlite():
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


class TestIncidentsApi:
    """Tests the REST API endpoints of the Incidents module."""

    @pytest.mark.asyncio
    async def test_list_incidents_and_filters(self, client_with_sqlite):
        client, session_factory = client_with_sqlite

        # Seed 3 distinct incidents
        now = datetime.now(UTC)
        async with session_factory() as session:
            session.add_all(
                [
                    IncidentModel(
                        id="inc-1",
                        title="Order Service Error Spike",
                        service_name="order-service",
                        severity=IncidentSeverity.CRITICAL.value,
                        status=IncidentStatus.OPEN.value,
                        incident_type="http_5xx_rate",
                        fingerprint="fp-api-1",
                        triggered_at=now,
                        last_seen_at=now,
                        created_at=now,
                        updated_at=now,
                    ),
                    IncidentModel(
                        id="inc-2",
                        title="Payment Service Latency Spike",
                        service_name="payment-service",
                        severity=IncidentSeverity.HIGH.value,
                        status=IncidentStatus.ACKNOWLEDGED.value,
                        incident_type="p95_latency",
                        fingerprint="fp-api-2",
                        triggered_at=now,
                        last_seen_at=now,
                        created_at=now,
                        updated_at=now,
                    ),
                    IncidentModel(
                        id="inc-3",
                        title="Old Gateway Downtime",
                        service_name="api-gateway",
                        severity=IncidentSeverity.MEDIUM.value,
                        status=IncidentStatus.RESOLVED.value,
                        incident_type="service_unavailable",
                        fingerprint="fp-api-3",
                        triggered_at=now,
                        last_seen_at=now,
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            await session.commit()

        # 1. List all
        resp = await client.get("/api/v1/incidents")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 3

        # 2. Filter by status: active (OPEN, ACKNOWLEDGED, INVESTIGATING)
        resp_act = await client.get("/api/v1/incidents?status=active")
        assert resp_act.status_code == 200
        assert len(resp_act.json()) == 2

        # 3. Filter by severity: CRITICAL
        resp_crit = await client.get("/api/v1/incidents?severity=CRITICAL")
        assert resp_crit.status_code == 200
        assert len(resp_crit.json()) == 1
        assert resp_crit.json()[0]["id"] == "inc-1"

        # 4. Filter by service: payment-service
        resp_svc = await client.get("/api/v1/incidents?service=payment-service")
        assert resp_svc.status_code == 200
        assert len(resp_svc.json()) == 1
        assert resp_svc.json()[0]["id"] == "inc-2"

        # 5. Invalid status filter returns 400 Bad Request
        resp_bad_st = await client.get("/api/v1/incidents?status=INVALID_STATUS")
        assert resp_bad_st.status_code == 400
        assert "Invalid status" in resp_bad_st.json()["detail"]

        # 6. Invalid severity filter returns 400 Bad Request
        resp_bad_sev = await client.get("/api/v1/incidents?severity=ULTRA_CRITICAL")
        assert resp_bad_sev.status_code == 400
        assert "Invalid severity" in resp_bad_sev.json()["detail"]

    @pytest.mark.asyncio
    async def test_get_incident_detail_and_404(self, client_with_sqlite):
        client, session_factory = client_with_sqlite

        now = datetime.now(UTC)
        async with session_factory() as session:
            inc = IncidentModel(
                id="inc-detail-test",
                title="Database Pool Saturation",
                service_name="order-service",
                severity=IncidentSeverity.HIGH.value,
                status=IncidentStatus.OPEN.value,
                incident_type="db_pool_saturation",
                fingerprint="fp-detail-test",
                evidence={"db_pool_size": 10, "checked_out": 8},
                triggered_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(inc)
            await session.commit()

        # Valid detail request
        resp = await client.get("/api/v1/incidents/inc-detail-test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "inc-detail-test"
        assert data["evidence"]["checked_out"] == 8

        # Non-existent ID returns 404
        resp_404 = await client.get("/api/v1/incidents/non-existent-id")
        assert resp_404.status_code == 404

    @pytest.mark.asyncio
    async def test_get_rules_and_stats(self, client_with_sqlite):
        client, session_factory = client_with_sqlite

        # Test /rules
        resp_rules = await client.get("/api/v1/incidents/rules")
        assert resp_rules.status_code == 200
        rules = resp_rules.json()
        assert len(rules) >= 6
        rule_ids = {r["rule_id"] for r in rules}
        assert "payment_failure_spike" in rule_ids
        assert "high_p95_latency" in rule_ids

        # Test /stats
        resp_stats = await client.get("/api/v1/incidents/stats")
        assert resp_stats.status_code == 200
        stats = resp_stats.json()
        assert "open_incidents" in stats
        assert "critical_incidents" in stats
        assert "by_service" in stats

    @pytest.mark.asyncio
    async def test_post_evaluate_endpoint(self, client_with_sqlite):
        client, _ = client_with_sqlite

        mock_res = DetectionRunResponse(
            status="success",
            rules_evaluated=6,
            breaches_detected=0,
            incidents_created=0,
            incidents_updated=0,
            incidents_resolved=0,
            evaluation_duration_ms=12.5,
        )

        with patch(
            "app.services.detection.engine.DetectionEngine.evaluate_all",
            new_callable=AsyncMock,
            return_value=mock_res,
        ):
            resp = await client.post("/api/v1/incidents/evaluate")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert data["rules_evaluated"] == 6
