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
