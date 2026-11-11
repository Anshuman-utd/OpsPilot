"""Comprehensive Test Suite for Phase 5.1 Checkpoint 1: Redis Distributed Locking.

Covers:
1. Successful Redis lock acquisition.
2. Lock contention between two workers.
3. Unique ownership tokens.
4. Owner releases its own lock.
5. Non-owner cannot release a lock.
6. Expired owner cannot delete a successor's lock.
7. Owner can renew a lease.
8. Non-owner cannot renew a lease.
9. Redis unavailable during acquisition — action never executes.
10. Redis unavailable during renewal — safe failure.
11. Redis unavailable during release — no false success.
12. Handler raises an exception — cleanup occurs.
13. Cancellation during execution — safe cleanup.
14. Duplicate task delivery — no duplicate side effects.
15. Two independent executor instances targeting the same service — no concurrent action execution.
16. Different services — independent locks.
17. Ownership lost before handler dispatch — handler never called.
18. No fallback to _in_memory_locks.
19. Existing policy-denied actions never reach execution.
20. Target service allowlist validation.
21. Live Redis integration test (skips cleanly if live Redis is unavailable).
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.remediation.executor as executor_module
from app.core.redis import ping_redis
from app.remediation.distributed_lock import (
    RedisUnavailableError,
    RemediationDistributedLock,
)
from app.remediation.executor import RemediationExecutor
from app.remediation.models import (
    ActionStatus,
    ApprovalDecision,
    ExecutionStatus,
    PlanStatus,
    RemediationActionModel,
    RemediationApprovalModel,
    RemediationExecutionModel,
    RemediationPlanModel,
    RiskLevel,
)
from tests.fake_redis import FakeRedis


# ---------------------------------------------------------------------------
# Helper to build a test execution DB record
# ---------------------------------------------------------------------------
def _build_test_records(
    service_name: str = "payment-service",
    action_type: str = "disable_fault_injection",
    status: str = ExecutionStatus.PENDING.value,
) -> tuple[RemediationPlanModel, RemediationActionModel, RemediationApprovalModel, RemediationExecutionModel]:
    plan_id = str(uuid.uuid4())
    action_id = str(uuid.uuid4())
    approval_id = str(uuid.uuid4())
    execution_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    plan = RemediationPlanModel(
        id=plan_id,
        incident_id="inc-test",
        status=PlanStatus.APPROVED.value,
        version=1,
        summary="Test Plan",
    )
    action = RemediationActionModel(
        id=action_id,
        plan_id=plan_id,
        action_type=action_type,
        target_service=service_name,
        environment="demo",
        parameters={"fault_type": "payment_failure_spike"},
        rationale="Disable test fault",
        expected_outcome="Restored",
        risk_level=RiskLevel.LOW.value,
        status=ActionStatus.APPROVED.value,
    )
    approval = RemediationApprovalModel(
        id=approval_id,
        plan_id=plan_id,
        action_id=action_id,
        decision=ApprovalDecision.APPROVED.value,
        plan_version=1,
        expires_at=now + timedelta(minutes=30),
    )
    execution = RemediationExecutionModel(
        id=execution_id,
        plan_id=plan_id,
        action_id=action_id,
        approval_id=approval_id,
        idempotency_key=f"exec-{action_id}-v1",
        status=status,
    )
    execution.action = action
    execution.plan = plan
    execution.approval = approval
    return plan, action, approval, execution


# ---------------------------------------------------------------------------
# Tests 1-8: Core Distributed Lock Semantics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lock_1_successful_acquisition():
    """1. Successful Redis lock acquisition sets key and assigns ownership."""
    fake_redis = FakeRedis()
    lock = RemediationDistributedLock("payment-service", ttl_seconds=10.0, redis_client=fake_redis)

    acquired = await lock.acquire()
    assert acquired is True
    assert lock.is_owned is True
    assert lock.ownership_token is not None
    assert await fake_redis.get("lock:remediation:payment-service") == lock.ownership_token
    await lock.release()


@pytest.mark.asyncio
async def test_lock_2_contention_between_two_workers():
    """2. Lock contention: Second worker fails to acquire already-held lock."""
    fake_redis = FakeRedis()
    worker1 = RemediationDistributedLock("payment-service", ttl_seconds=10.0, redis_client=fake_redis)
    worker2 = RemediationDistributedLock("payment-service", ttl_seconds=10.0, redis_client=fake_redis)

    assert await worker1.acquire() is True
    assert await worker2.acquire() is False

    assert worker1.is_owned is True
    assert worker2.is_owned is False
    # Value in Redis remains Worker 1's token
    assert await fake_redis.get("lock:remediation:payment-service") == worker1.ownership_token

    await worker1.release()


@pytest.mark.asyncio
async def test_lock_3_unique_ownership_tokens():
    """3. Unique ownership tokens: Every acquisition attempt gets a fresh UUID."""
    fake_redis = FakeRedis()
    lock1 = RemediationDistributedLock("payment-service", redis_client=fake_redis)
    assert await lock1.acquire() is True
    token1 = lock1.ownership_token
    await lock1.release()

    lock2 = RemediationDistributedLock("payment-service", redis_client=fake_redis)
    assert await lock2.acquire() is True
    token2 = lock2.ownership_token
    await lock2.release()

    assert token1 != token2
    assert uuid.UUID(token1)
    assert uuid.UUID(token2)


@pytest.mark.asyncio
async def test_lock_4_owner_releases_its_own_lock():
    """4. Owner releases its own lock atomically."""
    fake_redis = FakeRedis()
    lock = RemediationDistributedLock("order-service", redis_client=fake_redis)
    await lock.acquire()
    assert await fake_redis.get("lock:remediation:order-service") is not None

    released = await lock.release()
    assert released is True
    assert lock.is_owned is False
    assert await fake_redis.get("lock:remediation:order-service") is None


@pytest.mark.asyncio
async def test_lock_5_non_owner_cannot_release_lock():
    """5. Non-owner cannot release a lock owned by another instance."""
    fake_redis = FakeRedis()
    worker1 = RemediationDistributedLock("payment-service", redis_client=fake_redis)
    worker2 = RemediationDistributedLock("payment-service", redis_client=fake_redis)

    await worker1.acquire()
    # Worker 2 attempts to release without owning it
    released = await worker2.release()
    assert released is False

    # Worker 1's lock is still safely held
    assert await fake_redis.get("lock:remediation:payment-service") == worker1.ownership_token
    assert await worker1.validate_ownership() is True
    await worker1.release()


@pytest.mark.asyncio
async def test_lock_6_expired_owner_cannot_delete_successor_lock():
    """6. Expired owner cannot delete a successor's lock (the critical race condition)."""
    fake_redis = FakeRedis()
    worker1 = RemediationDistributedLock("payment-service", ttl_seconds=1.0, redis_client=fake_redis)
    worker2 = RemediationDistributedLock("payment-service", ttl_seconds=10.0, redis_client=fake_redis)

    # 1. Worker 1 acquires lock
    assert await worker1.acquire() is True
    token1 = worker1.ownership_token

    # 2. Worker 1's lock expires due to TTL
    fake_redis.advance_time(2.0)

    # 3. Worker 2 acquires the now-free lock
    assert await worker2.acquire() is True
    token2 = worker2.ownership_token
    assert token1 != token2

    # 4. Worker 1 finishes late and attempts release
    rel1 = await worker1.release()
    assert rel1 is False  # Safe Lua compare-and-delete rejected release!

    # 5. Worker 2's lock is still intact!
    assert await fake_redis.get("lock:remediation:payment-service") == token2
    assert await worker2.validate_ownership() is True
    await worker2.release()


@pytest.mark.asyncio
async def test_lock_7_owner_can_renew_lease():
    """7. Owner can extend its lock lease atomically."""
    fake_redis = FakeRedis()
    lock = RemediationDistributedLock("order-service", ttl_seconds=5.0, redis_client=fake_redis)
    await lock.acquire()

    # Advance time 3 seconds
    fake_redis.advance_time(3.0)

    renewed = await lock.renew()
    assert renewed is True
    assert await lock.validate_ownership() is True

    # Advance time another 3 seconds (total 6s from initial acquire; without renewal it would have expired)
    fake_redis.advance_time(3.0)
    assert await lock.validate_ownership() is True
    await lock.release()


@pytest.mark.asyncio
async def test_lock_8_non_owner_cannot_renew_lease():
    """8. Non-owner cannot renew another worker's lock lease."""
    fake_redis = FakeRedis()
    worker1 = RemediationDistributedLock("payment-service", ttl_seconds=5.0, redis_client=fake_redis)
    worker2 = RemediationDistributedLock("payment-service", ttl_seconds=5.0, redis_client=fake_redis)

    await worker1.acquire()
    renewed = await worker2.renew()
    assert renewed is False
    await worker1.release()


# ---------------------------------------------------------------------------
# Tests 9-11: Redis Failure & Outage Handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lock_9_redis_unavailable_during_acquisition_action_never_executes(sqlite_session: AsyncSession):
    """9. Redis unavailable during acquisition: fail-closed, action never executes."""
    plan, action, approval, execution = _build_test_records("payment-service")
    sqlite_session.add_all([plan, action, approval, execution])
    await sqlite_session.commit()

    engine = sqlite_session.bind
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    failing_redis = FakeRedis()
    failing_redis.should_fail = True

    executor = RemediationExecutor(redis_client=failing_redis)

    mock_handler = AsyncMock(return_value={"success": True})
    with patch.object(executor.registry.get("disable_fault_injection"), "handler", mock_handler):
        res = await executor.execute_action(execution.id, session_factory=session_factory)

    assert res["status"] == "redis_unavailable"
    assert "Redis infrastructure failure" in res["error"]
    # Handler was NEVER invoked!
    mock_handler.assert_not_called()


@pytest.mark.asyncio
async def test_lock_10_redis_unavailable_during_renewal_safe_failure():
    """10. Redis unavailable during renewal causes controlled safe failure and stops renewal loop."""
    fake_redis = FakeRedis()
    lock = RemediationDistributedLock("payment-service", ttl_seconds=2.0, renewal_interval_seconds=0.1, redis_client=fake_redis)
    await lock.acquire()

    # Inject failure into Redis
    fake_redis.should_fail = True

    # Next renew should raise RedisUnavailableError and mark lock lost
    with pytest.raises(RedisUnavailableError):
        await lock.renew()

    assert lock.is_owned is False
    assert lock._lost_ownership is True


@pytest.mark.asyncio
async def test_lock_11_redis_unavailable_during_release_no_false_success():
    """11. Redis unavailable during release raises RedisUnavailableError without claiming success."""
    fake_redis = FakeRedis()
    lock = RemediationDistributedLock("payment-service", redis_client=fake_redis)
    await lock.acquire()

    fake_redis.should_fail = True
    with pytest.raises(RedisUnavailableError):
        await lock.release()

    # Local state safely marks ownership false
    assert lock.is_owned is False


# ---------------------------------------------------------------------------
# Tests 12-17: Execution Engine Integration & Concurrency Safety
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lock_12_handler_raises_exception_cleanup_occurs(sqlite_session: AsyncSession):
    """12. Handler raises exception: error is handled and lock is safely released in finally."""
    plan, action, approval, execution = _build_test_records("payment-service")
    sqlite_session.add_all([plan, action, approval, execution])
    await sqlite_session.commit()

    engine = sqlite_session.bind
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_redis = FakeRedis()
    executor = RemediationExecutor(redis_client=fake_redis)

    mock_handler = AsyncMock(side_effect=RuntimeError("Microservice connection reset by peer"))
    with patch.object(executor.registry.get("disable_fault_injection"), "handler", mock_handler):
        res = await executor.execute_action(execution.id, session_factory=session_factory)

    assert res["status"] == "failed"
    assert "connection reset" in res["error"]

    # Lock must be completely released in Redis despite exception
    assert await fake_redis.get("lock:remediation:payment-service") is None


@pytest.mark.asyncio
async def test_lock_13_cancellation_during_execution_safe_cleanup(sqlite_session: AsyncSession):
    """13. Task cancellation during execution: lock is released in finally block."""
    plan, action, approval, execution = _build_test_records("payment-service")
    sqlite_session.add_all([plan, action, approval, execution])
    await sqlite_session.commit()

    engine = sqlite_session.bind
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_redis = FakeRedis()
    executor = RemediationExecutor(redis_client=fake_redis)

    async def cancelled_handler(*args, **kwargs):
        raise asyncio.CancelledError()

    with (
        patch.object(executor.registry.get("disable_fault_injection"), "handler", side_effect=cancelled_handler),
        pytest.raises(asyncio.CancelledError),
    ):
        await executor.execute_action(execution.id, session_factory=session_factory)

    # Lock must be released in Redis even upon task cancellation
    assert await fake_redis.get("lock:remediation:payment-service") is None


@pytest.mark.asyncio
async def test_lock_14_duplicate_task_delivery_no_duplicate_side_effects(sqlite_session: AsyncSession):
    """14. Duplicate task delivery does not execute side effects twice."""
    plan, action, approval, execution = _build_test_records("payment-service")
    sqlite_session.add_all([plan, action, approval, execution])
    await sqlite_session.commit()

    engine = sqlite_session.bind
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_redis = FakeRedis()
    executor = RemediationExecutor(redis_client=fake_redis)

    mock_handler = AsyncMock(return_value={"success": True})
    with (
        patch.object(executor.registry.get("disable_fault_injection"), "handler", mock_handler),
        patch("app.workers.remediation_tasks.run_remediation_verification_task.delay"),
    ):
        res1 = await executor.execute_action(execution.id, session_factory=session_factory)
        assert res1["status"] == "success"

        # Redeliver same task
        res2 = await executor.execute_action(execution.id, session_factory=session_factory)
        assert res2["status"] == "duplicate_prevented"

    # Handler was only invoked ONCE
    assert mock_handler.call_count == 1


@pytest.mark.asyncio
async def test_lock_15_two_independent_executors_same_service_no_concurrent_execution(sqlite_session: AsyncSession):
    """15. Two independent executor instances on the same service: second is blocked by lock contention."""
    plan1, action1, approval1, execution1 = _build_test_records("payment-service")
    plan2, action2, approval2, execution2 = _build_test_records("payment-service")
    sqlite_session.add_all([plan1, action1, approval1, execution1, plan2, action2, approval2, execution2])
    await sqlite_session.commit()

    engine = sqlite_session.bind
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_redis = FakeRedis()
    executor1 = RemediationExecutor(redis_client=fake_redis)
    executor2 = RemediationExecutor(redis_client=fake_redis)

    barrier = asyncio.Event()

    async def slow_handler(*args, **kwargs):
        # Wait on barrier so executor 2 attempts acquisition while executor 1 is executing
        await barrier.wait()
        return {"success": True}

    with (
        patch.object(executor1.registry.get("disable_fault_injection"), "handler", side_effect=slow_handler),
        patch("app.workers.remediation_tasks.run_remediation_verification_task.delay"),
    ):
        task1 = asyncio.create_task(executor1.execute_action(execution1.id, session_factory=session_factory))

        # Give task1 time to acquire lock and enter slow_handler
        await asyncio.sleep(0.05)

        # Attempt executor2 execution
        res2 = await executor2.execute_action(execution2.id, session_factory=session_factory)
        assert res2["status"] == "locked"
        assert "is currently locked" in res2["message"]

        # Release task1 barrier
        barrier.set()
        res1 = await task1
        assert res1["status"] == "success"


@pytest.mark.asyncio
async def test_lock_16_different_services_independent_locks(sqlite_session: AsyncSession):
    """16. Different services use independent lock keys and do not block each other."""
    fake_redis = FakeRedis()
    lock_order = RemediationDistributedLock("order-service", redis_client=fake_redis)
    lock_payment = RemediationDistributedLock("payment-service", redis_client=fake_redis)

    assert await lock_order.acquire() is True
    assert await lock_payment.acquire() is True

    assert lock_order.lock_key != lock_payment.lock_key
    assert await lock_order.validate_ownership() is True
    assert await lock_payment.validate_ownership() is True

    await lock_order.release()
    await lock_payment.release()


@pytest.mark.asyncio
async def test_lock_17_ownership_lost_before_handler_dispatch_handler_never_called(sqlite_session: AsyncSession):
    """17. Ownership lost before handler dispatch: handler is never called."""
    plan, action, approval, execution = _build_test_records("payment-service")
    sqlite_session.add_all([plan, action, approval, execution])
    await sqlite_session.commit()

    engine = sqlite_session.bind
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_redis = FakeRedis()
    executor = RemediationExecutor(redis_client=fake_redis)

    mock_handler = AsyncMock(return_value={"success": True})

    # Evict the lock from Redis before handler dispatch
    orig_validate = RemediationDistributedLock.validate_ownership
    async def intercept_validate(self):
        # Simulate lock lost in Redis right before handler
        await fake_redis.delete(self.lock_key)
        return await orig_validate(self)

    with (
        patch.object(RemediationDistributedLock, "validate_ownership", intercept_validate),
        patch.object(executor.registry.get("disable_fault_injection"), "handler", mock_handler),
    ):
        res = await executor.execute_action(execution.id, session_factory=session_factory)

    assert res["status"] == "failed"
    assert "Lost lock lease ownership" in res["error"]
    mock_handler.assert_not_called()


# ---------------------------------------------------------------------------
# Tests 18-20: Allowlist, In-memory Elimination & Safety Policy
# ---------------------------------------------------------------------------

def test_lock_18_no_fallback_to_in_memory_locks():
    """18. Ensure _in_memory_locks is completely absent from the codebase."""
    assert not hasattr(executor_module, "_in_memory_locks")
    assert not hasattr(RemediationExecutor, "_acquire_lock")
    assert not hasattr(RemediationExecutor, "_release_lock")


@pytest.mark.asyncio
async def test_lock_19_policy_denied_actions_never_reach_execution(sqlite_session: AsyncSession):
    """19. Policy-denied action is rejected before lock acquisition is attempted."""
    plan, action, approval, execution = _build_test_records("payment-service")
    # Expire approval
    approval.expires_at = datetime.now(UTC) - timedelta(minutes=10)
    sqlite_session.add_all([plan, action, approval, execution])
    await sqlite_session.commit()

    engine = sqlite_session.bind
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_redis = FakeRedis()
    executor = RemediationExecutor(redis_client=fake_redis)

    res = await executor.execute_action(execution.id, session_factory=session_factory)
    assert res["status"] == "policy_rejected"
    # No lock was ever acquired
    assert await fake_redis.get("lock:remediation:payment-service") is None


def test_lock_20_service_allowlist_validation():
    """20. Validate that unallowlisted services are rejected when creating a lock."""
    invalid_services = [
        "../../etc/passwd",
        "attacker-service",
        "kubernetes",
        "database-service",
    ]
    for svc in invalid_services:
        with pytest.raises(ValueError) as exc:
            RemediationDistributedLock(svc)
        assert "Invalid or unallowlisted service name" in str(exc.value)


# ---------------------------------------------------------------------------
# Test 21: Live Redis Integration Test (Skipped honestly if no live Redis)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.live_redis
async def test_live_redis_contention_and_expired_owner_race():
    """21. Live Redis server integration test verifying atomic contention and ownership release safety."""
    is_live, details = await ping_redis()
    if not is_live:
        pytest.skip(f"Live Redis server unavailable at configured REDIS_URL: {details}")

    # Real live Redis client is used
    svc = "api-gateway"
    worker1 = RemediationDistributedLock(svc, ttl_seconds=1.0)
    worker2 = RemediationDistributedLock(svc, ttl_seconds=5.0)

    # 1. Atomic acquisition
    acq1 = await worker1.acquire()
    assert acq1 is True

    # 2. Atomic contention on real server
    acq2 = await worker2.acquire()
    assert acq2 is False

    # 3. Wait for Worker 1 TTL to expire on real Redis
    await asyncio.sleep(1.2)

    # 4. Worker 2 can now acquire
    acq2_retry = await worker2.acquire()
    assert acq2_retry is True

    # 5. Worker 1 late release: Lua compare-and-delete will not delete Worker 2's lock
    rel1 = await worker1.release()
    assert rel1 is False

    # 6. Worker 2's lock is still held on real server
    assert await worker2.validate_ownership() is True
    await worker2.release()
