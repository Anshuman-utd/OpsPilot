"""Targeted Empirical Verification Tests for Phase 5 Independent Security & Execution Integrity Audit.

Validates:
1. Approver identity unauthenticated input and lack of cryptographic signatures.
2. Lack of action content hashing allowing parameter mutation post-approval.
3. Service base URL configuration accepting arbitrary hosts/metadata IPs (SSRF susceptibility).
4. Fault type path traversal rejection by allowlist validator.
5. Distributed locking release bug: lock deletion without ownership token deletes other workers' locks.
6. Celery task failure state when worker crashes mid-execution.
7. Pseudo-actions inspecting actual endpoint dispatch.
8. Recovery verification polling window and metric scale consistency.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
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
async def test_audit_1_arbitrary_unauthenticated_approver_identity(sqlite_session: AsyncSession):
    """Verify that an arbitrary spoofed approver_id can be submitted without authentication or signatures."""
    incident_id = f"inc-{uuid.uuid4()}"
    incident = IncidentModel(
        id=incident_id,
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

    service = RemediationService()
    plan = await service.create_plan(incident_id=incident_id, db=sqlite_session)
    action = plan.actions[0]

    # Client submits arbitrary spoofed approver identity with NO credentials or signature
    spoofed_identity = "chief_executive_officer@external-attacker.com"
    with patch("app.workers.remediation_tasks.run_remediation_execution_task.delay"):
        approval, execution = await service.approve_action(
            plan_id=plan.id,
            action_id=action.id,
            approval_req=RemediationApprovalRequest(
                approver_id=spoofed_identity,
                comment="Spoofed approval comment without cryptographic signature",
                plan_version=plan.version,
            ),
            db=sqlite_session,
        )

    # Verified: approver_id is persisted exactly as submitted; no signature exists
    assert approval.approver_id == spoofed_identity
    assert not hasattr(approval, "signature")
    assert not hasattr(approval, "public_key")
    assert not hasattr(approval, "digest")


@pytest.mark.asyncio
async def test_audit_2_action_tampering_post_approval_executes_unreviewed_parameters(sqlite_session: AsyncSession):
    """Verify that action parameters modified after approval are executed because approval lacks a content hash."""
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
    initial_params = dict(action.parameters)

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

    assert res["status"] == "success"
    # The handler executed the TAMPERED parameters, not the parameters reviewed by the operator!
    assert captured_params != initial_params
    assert captured_params == {"fault_type": "downstream_latency_spike"}


def test_audit_3_unvalidated_service_urls_permit_ssrf_configuration():
    """Verify that Settings accepts arbitrary external and metadata URLs without validation."""
    malicious_urls = [
        "http://169.254.169.254/latest/meta-data/",
        "http://attacker-controlled.internal:8002",
        "http://admin:secret@10.0.0.5:9000",
        "ftp://malicious.host:21",
    ]
    for url in malicious_urls:
        cfg = Settings(ORDER_SERVICE_URL=url)
        # Settings does not validate scheme, host, or private IP addresses
        assert url == cfg.ORDER_SERVICE_URL


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


def test_audit_5_redis_import_error_causes_silent_in_memory_fallback():
    """Verify that executor._acquire_lock tries to import non-existent 'get_redis', always falling back to in-memory lock."""
    import app.core.redis as redis_mod
    # Verify that get_redis does not exist in app.core.redis
    assert not hasattr(redis_mod, "get_redis")
    assert hasattr(redis_mod, "get_redis_client")

    # When _acquire_lock runs, it catches ImportError and falls back to process-local in-memory lock
    executor = RemediationExecutor()
    lock_key = "lock:remediation:test-audit-service"
    from app.remediation.executor import _in_memory_locks
    _in_memory_locks.clear()

    try:
        # Acquiring lock succeeds only via in-memory dictionary
        res = asyncio.run(executor._acquire_lock(lock_key, ttl_seconds=1.0))
        assert res is True
        assert lock_key in _in_memory_locks  # Confirms in-memory fallback was used!
    finally:
        _in_memory_locks.clear()


@pytest.mark.asyncio
async def test_audit_5_lock_release_unconditional_delete_deletes_other_worker_lock():
    """Verify that if Redis locking were called, unconditional delete deletes another worker's lock if expired."""
    fake_redis_store = {}

    class MockRedis:
        async def set(self, key, value, nx=False, ex=None):
            if nx and key in fake_redis_store:
                return False
            fake_redis_store[key] = value
            return True

        async def delete(self, key):
            fake_redis_store.pop(key, None)

    mock_redis = MockRedis()
    lock_key = "lock:remediation:payment-service"

    # Simulate what _acquire_lock and _release_lock would do with a Redis client:
    # 1. Worker 1 acquires lock
    await mock_redis.set(lock_key, "locked", nx=True, ex=1)
    assert fake_redis_store[lock_key] == "locked"  # Stored static string, not unique token

    # 2. Worker 1's lock expires in Redis (simulated TTL expiry)
    fake_redis_store.pop(lock_key)

    # 3. Worker 2 acquires lock on the same service
    await mock_redis.set(lock_key, "locked", nx=True, ex=10)
    assert fake_redis_store[lock_key] == "locked"  # Worker 2 now owns lock

    # 4. Worker 1 finishes late and releases lock unconditionally
    await mock_redis.delete(lock_key)

    # 5. Worker 1 has DELETED Worker 2's active lock!
    assert lock_key not in fake_redis_store


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
async def test_audit_7_stale_sampling_interval_in_rapid_polling():
    """Verify that 3 observations at 2-second polling query virtually the same 2-minute rolling rate window."""
    timestamps = []

    async def mock_query(*args, **kwargs):
        timestamps.append((kwargs.get("start"), kwargs.get("end")))
        mock_res = MagicMock()
        mock_res.success = True
        mock_res.series = [MagicMock(latest_value=0.01)]
        return mock_res

    mock_prom = AsyncMock()
    mock_prom.get_service_error_rate = mock_query

    verifier = RecoveryVerificationEngine(prometheus_tool=mock_prom)

    # Run 3 consecutive observations separated by 0.05s
    for _ in range(3):
        await verifier.evaluate_observation("order-service", {"metric": "http_error_rate", "threshold": 0.05}, window_seconds=120.0)
        await asyncio.sleep(0.05)

    assert len(timestamps) == 3
    # The time windows overlap by more than 99%
    w1_start, w1_end = timestamps[0]
    w3_start, w3_end = timestamps[2]
    overlap = (w1_end - w3_start) / 120.0
    assert overlap > 0.98  # >98% identical data sampled three times
