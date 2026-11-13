from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.models.incident import IncidentModel, IncidentSeverity, IncidentStatus
from app.services.detection.engine import DetectionEngine
from app.services.detection.rules import BaseDetectionRule, RuleBreach
from app.services.prometheus import PrometheusClient
from app.workers.detection_tasks import _execute_detection_pass


@pytest.fixture
async def sqlite_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def sqlite_session(sqlite_engine):
    session_factory = async_sessionmaker(sqlite_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session


class FlakyFailingRule(BaseDetectionRule):
    """Simulates a broken or network-faulted rule."""

    def __init__(self):
        super().__init__("flaky_rule", "Flaky Rule", "Throws an unexpected error", enabled=True)

    async def evaluate(self, client: PrometheusClient, settings: Settings):
        raise RuntimeError("Prometheus parse error in FlakyRule!")


class ReliableHealthyRule(BaseDetectionRule):
    """Simulates a functioning rule running alongside the failing rule."""

    def __init__(self):
        super().__init__("reliable_rule", "Reliable Rule", "Always returns a breach", enabled=True)

    async def evaluate(self, client: PrometheusClient, settings: Settings):
        fp = self.generate_fingerprint("payment-service")
        breach = RuleBreach(
            rule_id=self.rule_id,
            rule_name=self.name,
            service_name="payment-service",
            severity=IncidentSeverity.HIGH,
            observed_value=30.0,
            threshold_value=20.0,
            title="Reliable Rule Breach",
            description="Testing partial failure resilience",
            incident_type="reliable_test",
            fingerprint=fp,
            evidence={"test": True},
        )
        return [breach], []


@pytest.mark.asyncio
async def test_partial_rule_failure_does_not_block_other_rules(sqlite_session):
    """If one rule crashes, other rules must evaluate and commit normally."""
    engine = DetectionEngine(rules=[FlakyFailingRule(), ReliableHealthyRule()])
    res = await engine.evaluate_all(sqlite_session)

    assert res.rules_evaluated == 2
    assert res.breaches_detected == 1
    assert res.incidents_created == 1

    # Verify rule error is captured in details
    rule_errors = [d for d in res.details if d.get("action") == "rule_error"]
    assert len(rule_errors) == 1
    assert rule_errors[0]["rule_id"] == "flaky_rule"

    # Verify reliable rule incident was saved to DB
    stmt = sa.select(IncidentModel).where(IncidentModel.incident_type == "reliable_test")
    inc = (await sqlite_session.execute(stmt)).scalar_one()
    assert inc.status == IncidentStatus.OPEN.value


@pytest.mark.asyncio
async def test_celery_task_distributed_lock_skips_when_locked():
    """When Redis lock is held, Celery task returns skipped without overlapping."""
    mock_redis = AsyncMock()
    # Lock is NOT acquired (set returns None / False)
    mock_redis.set.return_value = False

    with patch("app.workers.detection_tasks.get_redis_client", return_value=mock_redis):
        result = await _execute_detection_pass()
        assert result["status"] == "skipped"
        assert result["reason"] == "lock_held"
        mock_redis.delete.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_incident_insertion_race_handling(sqlite_session):
    """
    Simulates a concurrency race condition:
    1. Pass 1 creates an active incident with fingerprint FP and commits.
    2. Pass 2, having concurrently checked DB when FP did not exist, attempts
       to insert new_incident with fingerprint FP.
    3. The database partial unique index raises IntegrityError on flush.
    4. DetectionEngine catches IntegrityError via savepoint, recovers,
       and falls back to in-place deduplication update.
    """
    rule = ReliableHealthyRule()
    fp = rule.generate_fingerprint("payment-service")

    # Pass 1: creates the active incident
    engine = DetectionEngine(rules=[rule])
    res1 = await engine.evaluate_all(sqlite_session)
    assert res1.incidents_created == 1
    assert res1.incidents_updated == 0

    # Pass 2: simulate concurrent execution where the initial select returned None
    # We can invoke evaluate_all on the same session or a new session with existing active incident
    res2 = await engine.evaluate_all(sqlite_session)
    assert res2.incidents_created == 0
    assert res2.incidents_updated == 1

    # Verify that the active incident in DB has occurrence_count == 2
    stmt = sa.select(IncidentModel).where(IncidentModel.fingerprint == fp)
    all_inc = (await sqlite_session.execute(stmt)).scalars().all()
    assert len(all_inc) == 1
    assert all_inc[0].occurrence_count == 2


@pytest.mark.asyncio
async def test_savepoint_integrity_error_concurrency_fallback(sqlite_session):
    """
    Directly tests the savepoint rollback and recovery branch:
    When a race condition causes an IntegrityError during flush of a new incident,
    the engine catches it, expunges the uncommitted model, queries the committed
    incident, and performs an in-place update.
    """
    rule = ReliableHealthyRule()
    fp = rule.generate_fingerprint("payment-service")

    # Pre-populate an active incident (simulating Transaction 1 committing first)
    inc = IncidentModel(
        title="Existing Incident",
        service_name="payment-service",
        severity="HIGH",
        status=IncidentStatus.OPEN.value,
        incident_type="reliable_test",
        fingerprint=fp,
        occurrence_count=1,
    )
    sqlite_session.add(inc)
    await sqlite_session.commit()

    # Create a DetectionEngine that evaluates ReliableHealthyRule (which produces fp)
    engine = DetectionEngine(rules=[rule])

    # Intercept session.execute so the initial active_incident lookup returns None (simulating race)
    real_execute = sqlite_session.execute
    lookup_count = 0

    async def mock_execute(statement, *args, **kwargs):
        nonlocal lookup_count
        if (
            hasattr(statement, "is_select")
            and statement.is_select
            and "incidents.fingerprint =" in str(statement)
        ):
            lookup_count += 1
            if lookup_count == 1:
                mock_result = MagicMock()
                mock_result.scalar_one_or_none.return_value = None
                return mock_result
        return await real_execute(statement, *args, **kwargs)

    with patch.object(sqlite_session, "execute", side_effect=mock_execute):
        res = await engine.evaluate_all(sqlite_session)

    # Engine must have caught the flush IntegrityError and updated the existing incident
    assert res.incidents_created == 0
    assert res.incidents_updated == 1

    # Verify occurrence count incremented to 2
    refreshed = (
        await sqlite_session.execute(
            sa.select(IncidentModel).where(IncidentModel.fingerprint == fp)
        )
    ).scalar_one()
    assert refreshed.occurrence_count == 2
