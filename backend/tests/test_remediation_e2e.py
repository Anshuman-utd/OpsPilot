"""End-to-End Demonstration and Scenario Tests for Phase 5 Controlled Remediation.

Covers:
1. Scenario A: Payment Failure Spike (detection, investigation, plan rejection, plan re-proposal,
   human approval, controlled execution, multi-cycle verification, incident resolution).
2. Scenario B: DB Connection Pool Exhaustion (order-service pool reset, safety policy evaluation, recovery).
3. Scenario C: Telemetry Outage (Prometheus unreachable / empty, honest TELEMETRY_UNAVAILABLE status,
   incident remains unresolved).
4. Scenario D: Duplicate / Concurrent Execution Prevention (idempotency key enforcement, service lock).
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.investigation.models import InvestigationModel
from app.investigation.tools.prometheus_tools import MetricInvestigationResult, MetricSeriesData
from app.models.incident import IncidentModel, IncidentSeverity, IncidentStatus
from app.remediation.executor import RemediationExecutor
from app.remediation.models import (
    ActionStatus,
    ApprovalDecision,
    ExecutionStatus,
    PlanStatus,
    VerificationStatus,
)
from app.remediation.schemas import (
    RemediationApprovalRequest,
    RemediationPlanCreateRequest,
    RemediationRejectionRequest,
)
from app.remediation.service import RemediationService
from app.remediation.verifier import RecoveryVerificationEngine


@pytest.mark.asyncio
async def test_scenario_a_payment_failure_spike_lifecycle(sqlite_session: AsyncSession):
    """Scenario A: Full lifecycle with plan rejection, re-planning, human approval, execution, and verification."""
    incident_id = f"inc-{uuid.uuid4()}"
    now = datetime.now(UTC)

    # 1. Detected Incident (Phase 3)
    incident = IncidentModel(
        id=incident_id,
        title="Payment Service Failure Spike",
        description="Payment failure ratio exceeds 5% threshold",
        service_name="payment-service",
        severity=IncidentSeverity.HIGH.value,
        status=IncidentStatus.OPEN.value,
        incident_type="payment_failure_spike",
        source="prometheus",
        fingerprint=f"fp-{uuid.uuid4()}",
        triggered_value=0.25,
        threshold_value=0.05,
        triggered_at=now - timedelta(minutes=10),
        last_seen_at=now,
    )
    sqlite_session.add(incident)

    # 2. Completed Phase 4 Investigation
    investigation_id = f"inv-{uuid.uuid4()}"
    investigation = InvestigationModel(
        id=investigation_id,
        incident_id=incident_id,
        status="COMPLETED",
        final_report={
            "confidence_level": "HIGH",
            "executive_summary": "Simulated payment failure spike active in payment-service debug endpoints.",
            "hypotheses": [
                {
                    "hypothesis_id": "hyp-1",
                    "title": "Payment Failure Fault Injection Active",
                    "explanation": "Active fault injection in payment service causing artificial 500 responses.",
                    "confidence": "HIGH",
                    "affected_components": ["payment-service"],
                }
            ],
            "observed_facts": ["Payment error rate spiked from 0.01 to 0.25"],
        },
    )
    sqlite_session.add(investigation)
    await sqlite_session.commit()

    service = RemediationService()

    # 3. Formulate Plan 1
    plan1 = await service.create_plan(
        incident_id=incident_id,
        db=sqlite_session,
        request=RemediationPlanCreateRequest(investigation_id=investigation_id),
    )
    assert plan1.status == PlanStatus.PENDING_APPROVAL.value
    assert len(plan1.actions) == 1
    action1 = plan1.actions[0]
    assert action1.action_type == "disable_fault_injection"
    assert action1.target_service == "payment-service"

    # 4. Human Operator Rejects Plan 1 (e.g. requires billing sync)
    rejection = await service.reject_action(
        plan_id=plan1.id,
        action_id=action1.id,
        rejection_req=RemediationRejectionRequest(
            approver_id="alice-sre",
            comment="Hold execution: awaiting confirmation from payments team.",
        ),
        db=sqlite_session,
    )
    assert rejection.decision == ApprovalDecision.REJECTED.value
    assert plan1.status == PlanStatus.REJECTED.value
    assert action1.status == ActionStatus.REJECTED.value

    # 5. Formulate Plan 2 after operator sync
    plan2 = await service.create_plan(
        incident_id=incident_id,
        db=sqlite_session,
        request=RemediationPlanCreateRequest(
            investigation_id=investigation_id,
            custom_context="Billing team confirmed safe to restore payment service default configuration.",
        ),
    )
    assert plan2.status == PlanStatus.PENDING_APPROVAL.value
    assert plan2.id != plan1.id
    action2 = plan2.actions[0]

    # 6. Human Operator Approves Plan 2
    with patch("app.workers.remediation_tasks.run_remediation_execution_task.delay"):
        approval, execution = await service.approve_action(
            plan_id=plan2.id,
            action_id=action2.id,
            approval_req=RemediationApprovalRequest(
                approver_id="alice-sre",
                comment="Billing team confirmed. Execution authorized.",
                plan_version=plan2.version,
            ),
            db=sqlite_session,
        )
    assert approval.decision == ApprovalDecision.APPROVED.value
    assert execution.status == ExecutionStatus.PENDING.value
    assert execution.idempotency_key == f"exec-{action2.id}-v{plan2.version}"

    # 7. Asynchronous Execution by RemediationExecutor
    # Provide a session factory binding to this test session's engine
    engine = sqlite_session.bind
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    executor = RemediationExecutor()

    with patch.object(executor.registry.get("disable_fault_injection"), "handler", new_callable=AsyncMock) as mock_handler:
        mock_handler.return_value = {"success": True, "fault_type": "payment_failure_spike", "status": "disabled"}
        with patch("app.workers.remediation_tasks.run_remediation_verification_task.delay") as mock_verif_task:
            res = await executor.execute_action(execution.id, session_factory=session_factory)

    assert res["status"] == "success"
    mock_handler.assert_called_once()
    mock_verif_task.assert_called_once_with(execution.id)

    # Verify execution transitioned to VERIFYING in DB
    async with session_factory() as verify_db:
        refreshed_exec = await service.get_execution(execution.id, verify_db)
        assert refreshed_exec.status == ExecutionStatus.VERIFYING.value
        assert refreshed_exec.execution_output["success"] is True

    # 8. Recovery Verification Multi-Cycle Evaluation
    # 3 consecutive observations under 0.05 threshold -> RECOVERED
    mock_tool = AsyncMock()
    mock_tool.get_payment_failure_ratio.return_value = MetricInvestigationResult(
        tool_name="prometheus",
        query="payment_failure_ratio",
        service="payment-service",
        success=True,
        series=[
            MetricSeriesData(
                labels={"service": "payment-service"},
                points=[(100.0, 0.01), (200.0, 0.008)],
                latest_value=0.008,
            )
        ],
    )

    verifier = RecoveryVerificationEngine(prometheus_tool=mock_tool)
    verif_res = await verifier.verify_recovery(
        execution.id,
        session_factory=session_factory,
        max_iterations=4,
        poll_interval_seconds=0.01,
    )

    assert verif_res == VerificationStatus.RECOVERED

    # Assert incident was automatically marked RESOLVED
    async with session_factory() as verify_db:
        refreshed_inc = (
            await verify_db.execute(select(IncidentModel).where(IncidentModel.id == incident_id))
        ).scalar_one()
        assert refreshed_inc.status == IncidentStatus.RESOLVED.value


@pytest.mark.asyncio
async def test_scenario_b_db_pool_exhaustion_recovery(sqlite_session: AsyncSession):
    """Scenario B: Order-service DB pool exhaustion remediation and recovery."""
    incident_id = f"inc-{uuid.uuid4()}"
    now = datetime.now(UTC)

    incident = IncidentModel(
        id=incident_id,
        title="Order Service DB Pool Saturated",
        description="Order service database connection pool saturated at 95%",
        service_name="order-service",
        severity=IncidentSeverity.CRITICAL.value,
        status=IncidentStatus.OPEN.value,
        incident_type="db_pool_saturation",
        source="prometheus",
        fingerprint=f"fp-{uuid.uuid4()}",
        triggered_value=0.95,
        threshold_value=0.85,
        triggered_at=now - timedelta(minutes=5),
        last_seen_at=now,
    )
    sqlite_session.add(incident)

    investigation = InvestigationModel(
        id=f"inv-{uuid.uuid4()}",
        incident_id=incident_id,
        status="COMPLETED",
        final_report={
            "confidence_level": "HIGH",
            "executive_summary": "Order service DB connection pool leaking connections.",
            "hypotheses": [
                {
                    "hypothesis_id": "hyp-db",
                    "title": "Database Connection Pool Saturation",
                    "explanation": "Connection pool exhausted; resetting pool reclaims all leaked sockets.",
                    "confidence": "HIGH",
                    "affected_components": ["order-service"],
                }
            ],
            "observed_facts": ["DB pool utilization at 95%"],
        },
    )
    sqlite_session.add(investigation)
    await sqlite_session.commit()

    service = RemediationService()
    plan = await service.create_plan(
        incident_id=incident_id,
        db=sqlite_session,
        request=RemediationPlanCreateRequest(investigation_id=investigation.id),
    )
    assert plan.status == PlanStatus.PENDING_APPROVAL.value
    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.action_type == "reset_db_connection_pool"
    assert action.target_service == "order-service"

    # Approve
    with patch("app.workers.remediation_tasks.run_remediation_execution_task.delay"):
        _, execution = await service.approve_action(
            plan_id=plan.id,
            action_id=action.id,
            approval_req=RemediationApprovalRequest(
                approver_id="bob-sre",
                comment="Reset connection pool to clear leaked connections.",
                plan_version=plan.version,
            ),
            db=sqlite_session,
        )

    # Execute
    engine = sqlite_session.bind
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    executor = RemediationExecutor()

    with patch.object(executor.registry.get("reset_db_connection_pool"), "handler", new_callable=AsyncMock) as mock_handler:
        mock_handler.return_value = {"success": True, "service": "order-service", "active_connections": 5}
        with patch("app.workers.remediation_tasks.run_remediation_verification_task.delay"):
            exec_res = await executor.execute_action(execution.id, session_factory=session_factory)

    assert exec_res["status"] == "success"

    # Verify recovery: db_pool_saturation drops to 0.20 (< 0.85)
    mock_tool = AsyncMock()
    mock_tool.get_db_pool_saturation.return_value = MetricInvestigationResult(
        tool_name="prometheus",
        query="db_pool_saturation",
        service="order-service",
        success=True,
        series=[
            MetricSeriesData(
                labels={"service": "order-service"},
                points=[(100.0, 0.20), (200.0, 0.18)],
                latest_value=0.18,
            )
        ],
    )

    verifier = RecoveryVerificationEngine(prometheus_tool=mock_tool)
    verif_res = await verifier.verify_recovery(
        execution.id,
        session_factory=session_factory,
        max_iterations=4,
        poll_interval_seconds=0.01,
    )
    assert verif_res == VerificationStatus.RECOVERED


@pytest.mark.asyncio
async def test_scenario_c_telemetry_outage_honesty(sqlite_session: AsyncSession):
    """Scenario C: Telemetry outage prevents false recovery; system reports TELEMETRY_UNAVAILABLE honestly."""
    incident_id = f"inc-{uuid.uuid4()}"
    now = datetime.now(UTC)

    incident = IncidentModel(
        id=incident_id,
        title="Payment Service Degradation",
        description="High latency and errors",
        service_name="payment-service",
        severity=IncidentSeverity.HIGH.value,
        status=IncidentStatus.OPEN.value,
        incident_type="payment_failure_spike",
        source="prometheus",
        fingerprint=f"fp-{uuid.uuid4()}",
        triggered_at=now - timedelta(minutes=5),
        last_seen_at=now,
    )
    sqlite_session.add(incident)
    await sqlite_session.commit()

    service = RemediationService()
    plan = await service.create_plan(incident_id=incident_id, db=sqlite_session)
    action = plan.actions[0]

    with patch("app.workers.remediation_tasks.run_remediation_execution_task.delay"):
        _, execution = await service.approve_action(
            plan_id=plan.id,
            action_id=action.id,
            approval_req=RemediationApprovalRequest(approver_id="carol-sre", comment="Approved"),
            db=sqlite_session,
        )

    engine = sqlite_session.bind
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    executor = RemediationExecutor()

    with patch.object(executor.registry.get(action.action_type), "handler", new_callable=AsyncMock) as mock_h:
        mock_h.return_value = {"success": True}
        with patch("app.workers.remediation_tasks.run_remediation_verification_task.delay"):
            await executor.execute_action(execution.id, session_factory=session_factory)

    # Prometheus is unreachable / returns empty result
    mock_tool = AsyncMock()
    mock_tool.get_payment_failure_ratio.side_effect = RuntimeError("Prometheus 502 Bad Gateway / Connection Refused")
    mock_tool.get_service_error_rate.side_effect = RuntimeError("Prometheus 502 Bad Gateway / Connection Refused")

    verifier = RecoveryVerificationEngine(prometheus_tool=mock_tool)
    verif_res = await verifier.verify_recovery(
        execution.id,
        session_factory=session_factory,
        max_iterations=2,
        poll_interval_seconds=0.01,
    )

    # Must NOT claim recovery
    assert verif_res == VerificationStatus.TELEMETRY_UNAVAILABLE

    # Incident must NOT be resolved
    refreshed_inc = (
        await sqlite_session.execute(select(IncidentModel).where(IncidentModel.id == incident_id))
    ).scalar_one()
    assert refreshed_inc.status == IncidentStatus.OPEN.value


@pytest.mark.asyncio
async def test_scenario_d_duplicate_and_concurrent_execution_prevention(sqlite_session: AsyncSession):
    """Scenario D: Idempotency enforcement and distributed locking prevent duplicate executions."""
    incident_id = f"inc-{uuid.uuid4()}"
    incident = IncidentModel(
        id=incident_id,
        title="Gateway Error Spike",
        service_name="api-gateway",
        severity=IncidentSeverity.MEDIUM.value,
        status=IncidentStatus.OPEN.value,
        incident_type="http_error_spike",
        source="prometheus",
        fingerprint=f"fp-{uuid.uuid4()}",
    )
    sqlite_session.add(incident)
    await sqlite_session.commit()

    service = RemediationService()
    plan = await service.create_plan(incident_id=incident_id, db=sqlite_session)
    action = plan.actions[0]

    with patch("app.workers.remediation_tasks.run_remediation_execution_task.delay"):
        _, execution = await service.approve_action(
            plan_id=plan.id,
            action_id=action.id,
            approval_req=RemediationApprovalRequest(approver_id="dan-sre", comment="Approved"),
            db=sqlite_session,
        )

    # Repeated approval returns the original execution without enqueuing a second task.
    with patch("app.workers.remediation_tasks.run_remediation_execution_task.delay") as enqueue:
        _, repeated = await service.approve_action(
            plan_id=plan.id,
            action_id=action.id,
            approval_req=RemediationApprovalRequest(approver_id="dan-sre", comment="Duplicate approval attempt"),
            db=sqlite_session,
        )
        assert repeated.id == execution.id
        enqueue.assert_not_called()

    engine = sqlite_session.bind
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    executor = RemediationExecutor()

    # First execution succeeds
    with patch.object(executor.registry.get(action.action_type), "handler", new_callable=AsyncMock) as mock_h:
        mock_h.return_value = {"success": True}
        with patch("app.workers.remediation_tasks.run_remediation_verification_task.delay"):
            res1 = await executor.execute_action(execution.id, session_factory=session_factory)

    assert res1["status"] == "success"

    # Second execution call with the SAME execution ID must be blocked as duplicate
    res2 = await executor.execute_action(execution.id, session_factory=session_factory)
    assert res2["status"] == "duplicate_prevented"
