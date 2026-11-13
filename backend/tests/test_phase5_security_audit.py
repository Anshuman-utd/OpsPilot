"""Regression tests for remediation approval, execution boundaries, and recovery."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.database import get_db_session
from app.main import app
from app.models.incident import IncidentModel, IncidentSeverity, IncidentStatus
from app.remediation.executor import RemediationExecutor
from app.remediation.registry import (
    DisableFaultParameters,
    handle_reset_db_pool,
    handle_restore_config,
)
from app.remediation.schemas import RemediationApprovalRequest
from app.remediation.service import RemediationService
from app.remediation.verifier import RecoveryVerificationEngine


@pytest.mark.asyncio
async def test_operator_token_required_and_body_identity_ignored(async_client, sqlite_session: AsyncSession):
    incident = IncidentModel(
        title="Payment Service Degradation",
        service_name="payment-service",
        severity=IncidentSeverity.HIGH.value,
        status=IncidentStatus.OPEN.value,
        incident_type="payment_failure_spike",
        source="prometheus",
        fingerprint=f"fp-{uuid.uuid4()}",
    )
    sqlite_session.add(incident)
    await sqlite_session.commit()
    plan = await RemediationService().create_plan(incident_id=incident.id, db=sqlite_session)

    async def override_session():
        yield sqlite_session

    endpoint = f"/api/v1/remediation-plans/{plan.id}/actions/{plan.actions[0].id}/approve"
    request = {"approver_id": "spoofed-operator", "plan_version": plan.version}
    app.dependency_overrides[get_db_session] = override_session
    try:
        with (
            patch("app.remediation.router.get_settings", return_value=Settings(
                REMEDIATION_OPERATOR_ID="configured-operator",
                REMEDIATION_OPERATOR_TOKEN="test-token",
            )),
            patch("app.workers.remediation_tasks.run_remediation_execution_task.delay") as enqueue,
        ):
            response = await async_client.post(endpoint, json=request)
            assert response.status_code == 401
            enqueue.assert_not_called()
            response = await async_client.post(
                endpoint, json=request, headers={"Authorization": "Bearer test-token"}
            )
            assert response.status_code == 202
            assert response.json()["approver_id"] == "configured-operator"
            enqueue.assert_called_once()
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_action_tampering_post_approval_is_rejected(sqlite_session: AsyncSession):
    """Reject modified action parameters before dispatch."""
    incident_id = f"inc-{uuid.uuid4()}"
    incident = IncidentModel(
        id=incident_id,
        title="Payment Service Spike",
        service_name="payment-service",
        severity=IncidentSeverity.HIGH.value,
        status=IncidentStatus.OPEN.value,
        incident_type="payment_failure_spike",
        source="prometheus",
        fingerprint=f"fp-{uuid.uuid4()}",
    )
    sqlite_session.add(incident)
    await sqlite_session.commit()

    service = RemediationService()
    plan = await service.create_plan(incident_id=incident_id, db=sqlite_session)
    action = plan.actions[0]

    with patch("app.workers.remediation_tasks.run_remediation_execution_task.delay"):
        approval, execution = await service.approve_action(
            plan_id=plan.id,
            action_id=action.id,
            approval_req=RemediationApprovalRequest(
                approver_id="sre-operator",
                comment="Reviewed and approved initial parameters",
                plan_version=plan.version,
            ),
            db=sqlite_session,
        )

    # Tamper with action parameters in database after approval has occurred
    action.parameters = {"fault_type": "downstream_latency_spike"}
    await sqlite_session.commit()

    # Executor runs
    engine = sqlite_session.bind
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    executor = RemediationExecutor()

    captured_params = {}
    async def mock_handler(svc, params, timeout):
        nonlocal captured_params
        captured_params = dict(params)
        return {"success": True}

    with (
        patch.object(executor.registry.get("disable_fault_injection"), "handler", side_effect=mock_handler),
        patch("app.workers.remediation_tasks.run_remediation_verification_task.delay"),
    ):
        res = await executor.execute_action(execution.id, session_factory=session_factory)

    assert res["status"] == "policy_rejected"
    assert captured_params == {}


def test_service_urls_reject_non_demo_origins():
    """Reject arbitrary external origins and non-HTTP protocols."""
    malicious_urls = [
        "http://169.254.169.254/latest/meta-data/",
        "http://attacker-controlled.internal:8002",
        "http://admin:secret@10.0.0.5:9000",
        "ftp://malicious.host:21",
    ]
    for url in malicious_urls:
        with pytest.raises(ValidationError, match="demo-service HTTP origin"):
            Settings(ORDER_SERVICE_URL=url)


def test_audit_4_fault_type_path_traversal_blocked_by_allowlist():
    """Verify that path traversal in fault_type is rejected by Pydantic allowlist validation."""
    traversal_payloads = [
        "../../etc/passwd",
        "../admin/disable",
        "payment_failure_spike/../../../root",
    ]
    for payload in traversal_payloads:
        p = DisableFaultParameters(fault_type=payload)
        with pytest.raises(ValueError) as exc:
            p.validate_fault_type()
        assert "Invalid fault_type" in str(exc.value)


@pytest.mark.asyncio
async def test_audit_5_redis_import_error_causes_silent_in_memory_fallback():
    """Verify that Checkpoint 1 eliminates the broken import and completely removes in-memory fallback.

    Original Finding A & E:
    executor imported 'get_redis' which raised ImportError and silently used _in_memory_locks.

    Remediated Invariant:
    _in_memory_locks is completely removed from app.remediation.executor.
    Distributed lock uses get_redis_client and fails closed on Redis connection failure.
    """
    import app.core.redis as redis_mod
    import app.remediation.executor as exec_mod
    from app.remediation.distributed_lock import RedisUnavailableError, RemediationDistributedLock
    from tests.fake_redis import FakeRedis

    # 1. Verify get_redis_client exists in app.core.redis
    assert hasattr(redis_mod, "get_redis_client")

    # 2. Verify _in_memory_locks was completely removed from executor module
    assert not hasattr(exec_mod, "_in_memory_locks")
    assert not hasattr(exec_mod.RemediationExecutor, "_acquire_lock")

    # 3. Verify RemediationDistributedLock fails closed when Redis is unavailable
    broken_redis = FakeRedis()
    broken_redis.should_fail = True
    lock = RemediationDistributedLock("payment-service", redis_client=broken_redis)

    with pytest.raises(RedisUnavailableError) as exc_info:
        await lock.acquire()

    assert "Redis unavailable" in str(exc_info.value)
    assert not lock.is_owned


@pytest.mark.asyncio
async def test_audit_5_lock_release_unconditional_delete_deletes_other_worker_lock():
    """Verify that atomic Lua compare-and-delete prevents a stale/expired worker from deleting a successor's lock.

    Original Finding B & C:
    Static lock string 'locked' + unconditional redis.delete allowed expired Worker 1 to delete Worker 2's lock.

    Remediated Invariant:
    Lock tokens are unique UUIDs.
    Release executes Lua compare-and-delete: Worker 1 cannot delete Worker 2's lock.
    """
    from app.remediation.distributed_lock import RemediationDistributedLock
    from tests.fake_redis import FakeRedis

    fake_redis = FakeRedis()

    # 1. Worker 1 acquires lock on payment-service
    worker1_lock = RemediationDistributedLock("payment-service", ttl_seconds=1.0, redis_client=fake_redis)
    acq1 = await worker1_lock.acquire()
    assert acq1 is True
    assert worker1_lock.is_owned
    token1 = worker1_lock.ownership_token

    # 2. Simulate Worker 1's lock expiring (clock advances past TTL)
    fake_redis.advance_time(2.0)

    # 3. Worker 2 acquires lock on the same service
    worker2_lock = RemediationDistributedLock("payment-service", ttl_seconds=10.0, redis_client=fake_redis)
    acq2 = await worker2_lock.acquire()
    assert acq2 is True
    assert worker2_lock.is_owned
    token2 = worker2_lock.ownership_token
    assert token1 != token2  # Unique cryptographic ownership tokens!

    # 4. Worker 1 finishes late and attempts to release
    rel1 = await worker1_lock.release()
    # Release returned False because the token in Redis does not match Worker 1's token!
    assert rel1 is False

    # 5. Worker 2's lock was NOT deleted by Worker 1
    assert await fake_redis.get("lock:remediation:payment-service") == token2
    assert await worker2_lock.validate_ownership() is True


@pytest.mark.asyncio
async def test_audit_6_pseudo_actions_target_fault_endpoints_not_real_subsystems():
    """Verify that reset_db_pool and restore_config are wrappers around simulated fault endpoints."""
    # Test handle_reset_db_pool
    mock_client = AsyncMock()
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"name": "db_pool_exhaustion", "enabled": False}
    mock_client.post.return_value = mock_resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        res = await handle_reset_db_pool("order-service", {})
        # Assert the actual URL called was /debug/faults/db_pool_exhaustion/disable
        mock_client.post.assert_called_once_with("http://localhost:8002/debug/faults/db_pool_exhaustion/disable")
        assert res["action"] == "reset_db_connection_pool"

    # Test handle_restore_config
    mock_client2 = AsyncMock()
    mock_resp2 = MagicMock(status_code=200)
    mock_resp2.json.return_value = {"name": "downstream_latency_spike", "enabled": False}
    mock_client2.post.return_value = mock_resp2

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client2
        res2 = await handle_restore_config("payment-service", {"config_type": "all"})
        mock_client2.post.assert_called_once_with("http://localhost:8003/debug/faults/downstream_latency_spike/disable")
        assert res2["action"] == "restore_service_default_config"


@pytest.mark.asyncio
async def test_verifier_queries_a_current_point_instead_of_reusing_history():
    mock_prom = AsyncMock()
    mock_prom.get_service_error_rate.return_value = MagicMock(success=True, series=[])
    verifier = RecoveryVerificationEngine(prometheus_tool=mock_prom)

    with patch("app.remediation.verifier.time.time", return_value=120.0):
        observation = await verifier.evaluate_observation("order-service", {"metric": "http_error_rate"})

    mock_prom.get_service_error_rate.assert_awaited_once_with(service="order-service", start=120.0, end=120.0)
    assert observation["telemetry_available"] is False
    assert observation["healthy"] is False
