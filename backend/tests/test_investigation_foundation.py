from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.database import Base, get_db_session
from app.investigation.models import (
    AgentStatus,
    InvestigationAgentRunModel,
    InvestigationModel,
    InvestigationStatus,
    InvestigationTriggerType,
)
from app.investigation.providers import (
    GroqProvider,
    LLMConfigurationError,
    get_llm_provider,
)
from app.main import create_application
from app.models.incident import IncidentModel, IncidentSeverity, IncidentStatus


@pytest.fixture
async def sqlite_session():
    """In-memory SQLite async database session fixture."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session


@pytest.fixture
async def client_with_sqlite():
    """FastAPI test client with SQLite database override."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app = create_application()
    app.dependency_overrides[get_db_session] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, session_factory


class TestLLMProviderAbstraction:
    """Tests for provider-agnostic LLM interface and Groq configuration."""

    def test_unconfigured_groq_provider(self):
        settings = Settings(GROQ_API_KEY=None, LLM_PROVIDER="groq")
        provider = get_llm_provider(settings)
        assert isinstance(provider, GroqProvider)
        assert provider.is_configured() is False
        assert provider.get_provider_name() == "groq"

    @pytest.mark.asyncio
    async def test_invoke_unconfigured_provider_raises_configuration_error(self):
        settings = Settings(GROQ_API_KEY="", LLM_PROVIDER="groq")
        provider = get_llm_provider(settings)

        class DummyOutput(pytest.Item if False else object):
            pass

        with pytest.raises(LLMConfigurationError, match="Groq LLM provider is not configured"):
            await provider.invoke_structured(DummyOutput, messages=[])

    def test_configured_groq_provider(self):
        settings = Settings(GROQ_API_KEY="gsk_test_mock_key_123", LLM_PROVIDER="groq")
        provider = get_llm_provider(settings)
        assert provider.is_configured() is True
        assert provider.model_name == "llama-3.3-70b-versatile"

    def test_unsupported_provider_raises_error(self):
        settings = Settings(LLM_PROVIDER="unsupported_ai")
        with pytest.raises(LLMConfigurationError, match="Unsupported LLM provider"):
            get_llm_provider(settings)


class TestInvestigationPersistence:
    """Tests for InvestigationModel and InvestigationAgentRunModel database behavior."""

    @pytest.mark.asyncio
    async def test_investigation_lifecycle_persistence(self, sqlite_session):
        now = datetime.now(UTC)
        incident = IncidentModel(
            id="inc-test-01",
            title="Payment Failure Spike",
            service_name="payment-service",
            severity=IncidentSeverity.CRITICAL.value,
            status=IncidentStatus.OPEN.value,
            incident_type="payment_failure",
            fingerprint="fp-test-01",
            triggered_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        sqlite_session.add(incident)
        await sqlite_session.commit()

        # Create investigation
        inv = InvestigationModel(
            incident_id="inc-test-01",
            status=InvestigationStatus.PENDING.value,
            trigger_type=InvestigationTriggerType.MANUAL.value,
            model_provider="groq",
            model_name="llama-3.3-70b-versatile",
            created_at=now,
            updated_at=now,
        )
        sqlite_session.add(inv)
        await sqlite_session.commit()

        # Add an agent run
        agent_run = InvestigationAgentRunModel(
            investigation_id=inv.id,
            agent_name="metrics_investigator",
            status=AgentStatus.RUNNING.value,
            started_at=now,
            findings={"observed_failure_rate": 85.0},
            created_at=now,
        )
        sqlite_session.add(agent_run)
        await sqlite_session.commit()

        # Verify relationships
        stmt = sa.select(InvestigationModel).where(InvestigationModel.id == inv.id)
        saved = (await sqlite_session.execute(stmt)).scalar_one()
        assert saved.incident_id == "inc-test-01"
        assert len(saved.agent_runs) == 1
        assert saved.agent_runs[0].agent_name == "metrics_investigator"
        assert saved.agent_runs[0].findings["observed_failure_rate"] == 85.0


class TestInvestigationAPIBasics:
    """Tests for investigation REST endpoints."""

    @pytest.mark.asyncio
    async def test_start_investigation_missing_credentials_returns_400(self, client_with_sqlite):
        client, session_factory = client_with_sqlite

        now = datetime.now(UTC)
        async with session_factory() as session:
            inc = IncidentModel(
                id="inc-api-test-01",
                title="Service Outage",
                service_name="payment-service",
                severity=IncidentSeverity.CRITICAL.value,
                status=IncidentStatus.OPEN.value,
                incident_type="service_unavailable",
                fingerprint="fp-api-test-01",
                triggered_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(inc)
            await session.commit()

        # Attempt to start investigation when GROQ_API_KEY is not configured
        with patch("app.investigation.service.get_settings") as mock_settings:
            mock_settings.return_value = Settings(GROQ_API_KEY=None, INVESTIGATION_ENABLED=True)
            resp = await client.post("/api/v1/incidents/inc-api-test-01/investigations")
            assert resp.status_code == 400
            data = resp.json()
            assert "LLM provider 'groq' is not configured" in data["detail"]

    @pytest.mark.asyncio
    async def test_start_investigation_nonexistent_incident_returns_404(self, client_with_sqlite):
        client, _ = client_with_sqlite
        resp = await client.post("/api/v1/incidents/nonexistent-incident-id/investigations")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_investigations_empty(self, client_with_sqlite):
        client, _ = client_with_sqlite
        resp = await client.get("/api/v1/incidents/inc-any/investigations")
        assert resp.status_code == 200
        assert resp.json() == []
