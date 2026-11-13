"""Recovery Verification Engine for evaluating real telemetry after remediation execution.
Enforces multi-cycle consecutive healthy measurements before declaring service recovery.
Missing telemetry is NEVER treated as successful recovery.
"""

import asyncio
import logging
import math
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.investigation.tools.prometheus_tools import PrometheusInvestigationTool
from app.models.incident import IncidentEventModel, IncidentEventType, IncidentModel, IncidentStatus
from app.remediation.models import (
    ActionStatus,
    ExecutionStatus,
    PlanStatus,
    RemediationAuditEventModel,
    RemediationAuditEventType,
    RemediationExecutionModel,
    RemediationVerificationRunModel,
    VerificationStatus,
)

logger = logging.getLogger("opspilot.remediation.verifier")


class RecoveryVerificationEngine:
    """Evaluates telemetry metrics over time to verify actual incident recovery."""

    def __init__(self, prometheus_tool: PrometheusInvestigationTool | None = None) -> None:
        self.prometheus = prometheus_tool or PrometheusInvestigationTool()
        self.settings = get_settings()

    async def evaluate_observation(
        self,
        service_name: str,
        verification_conditions: dict[str, Any],
    ) -> dict[str, Any]:
        """Query Prometheus golden signals over the recent window to evaluate service health."""
        now = time.time()
        target_metric = verification_conditions.get("metric", "http_error_rate")
        threshold = float(verification_conditions.get("threshold", 0.05))
        observed_value = None
        details: dict[str, Any] = {"metric": target_metric}

        try:
            # A single evaluation at the current time cannot reuse older range points.
            if target_metric == "payment_failure_ratio":
                res = await self.prometheus.get_payment_failure_ratio(start=now, end=now)
            else:
                query = {
                    "db_pool_saturation": self.prometheus.get_db_pool_saturation,
                    "p95_latency": self.prometheus.get_service_p95_latency,
                    "http_error_rate": self.prometheus.get_service_error_rate,
                }[target_metric]
                res = await query(service=service_name, start=now, end=now)

            if res.success and res.series and all(math.isfinite(series.latest_value) for series in res.series):
                observed_value = max(series.latest_value for series in res.series)
            details.update(query=res.query, series_count=len(res.series))
            # In the deployed workspace, a good incident metric also requires a healthy scrape target.
            if self.settings.auth_required and observed_value is not None:
                target = await self.prometheus.client.query_instant(f'up{{service="{service_name}"}}')
                details["target_up"] = (
                    all(sample.value == 1.0 for sample in target.samples)
                    if target.success and target.samples else None
                )
        except Exception as exc:
            logger.warning("Telemetry evaluation failed for %s (%s): %s", service_name, target_metric, exc)
            details["error"] = str(exc)

        target_up = details.get("target_up", not self.settings.auth_required)
        telemetry_available = observed_value is not None and target_up is not None
        healthy = telemetry_available and bool(target_up) and observed_value is not None and observed_value <= threshold
        observation = {
            "timestamp": datetime.now(UTC).isoformat(),
            "telemetry_available": telemetry_available,
            "healthy": healthy,
            "observed_value": observed_value,
            "threshold": threshold,
            "details": details,
        }
        return observation

    async def verify_recovery(
        self,
        execution_id: str,
        session_factory: Any = None,
        max_iterations: int | None = None,
        poll_interval_seconds: float | None = None,
    ) -> VerificationStatus:
        """Execute the multi-cycle verification loop in background worker."""
        factory = session_factory or get_session_factory()
        now = datetime.now(UTC)
        interval = self.settings.REMEDIATION_VERIFICATION_INTERVAL_SECONDS
        if poll_interval_seconds is not None:
            interval = poll_interval_seconds
        deadline = time.monotonic() + self.settings.REMEDIATION_VERIFICATION_TIMEOUT_SECONDS

        async with factory() as db:
            stmt = (
                select(RemediationExecutionModel)
                .options(
                    selectinload(RemediationExecutionModel.action),
                    selectinload(RemediationExecutionModel.plan),
                    selectinload(RemediationExecutionModel.verification_runs),
                )
                .where(RemediationExecutionModel.id == execution_id)
            )
            execution = (await db.execute(stmt)).scalar_one_or_none()
            if not execution:
                logger.error("Execution '%s' not found for verification", execution_id)
                return VerificationStatus.VERIFICATION_FAILED

            if execution.status != ExecutionStatus.VERIFYING.value:
                if execution.verification_runs:
                    return VerificationStatus(execution.verification_runs[0].status)
                return VerificationStatus.VERIFICATION_FAILED

            action = execution.action
            plan = execution.plan
            service_name = action.target_service
            conds = action.verification_conditions or {}
            required_healthy = int(conds.get("consecutive_cycles", self.settings.REMEDIATION_CONSECUTIVE_HEALTHY_REQUIRED))

            # Retrieve or create verification run record
            verif_run = execution.verification_runs[0] if execution.verification_runs else None
            if not verif_run:
                verif_run = RemediationVerificationRunModel(
                    execution_id=execution.id,
                    status=VerificationStatus.VERIFYING.value,
                    started_at=now,
                    required_healthy_count=required_healthy,
                    consecutive_healthy_count=0,
                    observations=[],
                )
                db.add(verif_run)
                await db.commit()

        consecutive_healthy = verif_run.consecutive_healthy_count
        final_status = VerificationStatus(verif_run.status)
        if final_status == VerificationStatus.RECOVERED:
            max_iterations = 0

        # Verification polling loop
        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            iteration += 1
            try:
                async with asyncio.timeout(remaining):
                    obs = await self.evaluate_observation(
                        service_name=service_name,
                        verification_conditions=conds,
                    )
            except TimeoutError:
                break

            async with factory() as db:
                run_stmt = (
                    select(RemediationVerificationRunModel)
                    .where(RemediationVerificationRunModel.execution_id == execution_id)
                )
                v_record = (await db.execute(run_stmt)).scalar_one()

                obs_list = list(v_record.observations)
                obs_list.append(obs)
                v_record.observations = obs_list

                # 1. Telemetry Unavailable
                if not obs["telemetry_available"]:
                    logger.warning("Telemetry unavailable for %s during verification cycle %d", service_name, iteration)
                    consecutive_healthy = 0
                    v_record.consecutive_healthy_count = 0
                    v_record.status = VerificationStatus.TELEMETRY_UNAVAILABLE.value
                    final_status = VerificationStatus.TELEMETRY_UNAVAILABLE

                # 2. Healthy Observation
                elif obs["healthy"]:
                    consecutive_healthy += 1
                    v_record.consecutive_healthy_count = consecutive_healthy
                    logger.info(
                        "Verified healthy telemetry observation (%d/%d) for %s (val: %s, threshold: %s)",
                        consecutive_healthy,
                        required_healthy,
                        service_name,
                        obs.get("observed_value"),
                        obs.get("threshold"),
                    )

                    if consecutive_healthy >= required_healthy:
                        v_record.status = VerificationStatus.RECOVERED.value
                        v_record.completed_at = datetime.now(UTC)
                        v_record.summary = (
                            f"Service '{service_name}' verified recovered after {consecutive_healthy} consecutive healthy cycles. "
                            f"Latest telemetry: {obs.get('observed_value')} (threshold: {obs.get('threshold')})."
                        )
                        final_status = VerificationStatus.RECOVERED

                # 3. Degraded / Regression Detected
                else:
                    if consecutive_healthy > 0:
                        v_record.status = VerificationStatus.REGRESSION_DETECTED.value
                        final_status = VerificationStatus.REGRESSION_DETECTED
                        logger.warning("Regression detected during verification for %s after %d healthy cycles", service_name, consecutive_healthy)
                    else:
                        v_record.status = VerificationStatus.STILL_DEGRADED.value
                        final_status = VerificationStatus.STILL_DEGRADED
                    consecutive_healthy = 0
                    v_record.consecutive_healthy_count = 0

                await db.commit()

            if final_status == VerificationStatus.RECOVERED or iteration == max_iterations:
                break
            remaining = deadline - time.monotonic()
            if remaining <= interval:
                break
            await asyncio.sleep(interval)

        # Final state transition & audit persistence
        async with factory() as db:
            exec_stmt = (
                select(RemediationExecutionModel)
                .options(
                    selectinload(RemediationExecutionModel.action),
                    selectinload(RemediationExecutionModel.plan),
                    selectinload(RemediationExecutionModel.verification_runs),
                )
                .where(RemediationExecutionModel.id == execution_id)
                .with_for_update()
            )
            execution = (await db.execute(exec_stmt)).scalar_one()
            if execution.status != ExecutionStatus.VERIFYING.value:
                return VerificationStatus(execution.verification_runs[0].status)
            action = execution.action
            plan = execution.plan
            v_record = execution.verification_runs[0]

            if final_status == VerificationStatus.RECOVERED:
                execution.status = ExecutionStatus.RECOVERED.value
                action.status = ActionStatus.VERIFIED.value
                plan.status = PlanStatus.SUCCEEDED.value

                # Auto-resolve incident
                inc_stmt = select(IncidentModel).where(IncidentModel.id == plan.incident_id)
                inc_record = (await db.execute(inc_stmt)).scalar_one_or_none()
                if inc_record and inc_record.status != IncidentStatus.RESOLVED.value:
                    prev_status = inc_record.status
                    inc_record.status = IncidentStatus.RESOLVED.value
                    inc_record.resolved_at = datetime.now(UTC)
                    inc_record.updated_at = inc_record.resolved_at
                    inc_event = IncidentEventModel(
                        id=str(uuid.uuid4()),
                        incident_id=inc_record.id,
                        event_type=IncidentEventType.INCIDENT_RESOLVED.value,
                        from_status=prev_status,
                        to_status=IncidentStatus.RESOLVED.value,
                        message=f"Incident automatically resolved following verified telemetry recovery ({v_record.summary})",
                        actor="recovery_verifier",
                        created_at=datetime.now(UTC),
                    )
                    db.add(inc_event)

                # Audit recovery verification
                audit = RemediationAuditEventModel(
                    incident_id=plan.incident_id,
                    investigation_id=plan.investigation_id,
                    plan_id=plan.id,
                    action_id=action.id,
                    execution_id=execution.id,
                    event_type=RemediationAuditEventType.RECOVERY_VERIFIED.value,
                    actor="recovery_verifier",
                    details={
                        "summary": v_record.summary,
                        "consecutive_healthy_cycles": consecutive_healthy,
                        "required_healthy_cycles": required_healthy,
                    },
                )
                db.add(audit)

            elif final_status == VerificationStatus.TELEMETRY_UNAVAILABLE:
                execution.status = ExecutionStatus.VERIFICATION_FAILED.value
                action.status = ActionStatus.FAILED.value
                plan.status = PlanStatus.FAILED.value
                v_record.completed_at = datetime.now(UTC)
                v_record.summary = "Telemetry was unavailable during verification window. Automated recovery could not be proven."

                audit = RemediationAuditEventModel(
                    incident_id=plan.incident_id,
                    investigation_id=plan.investigation_id,
                    plan_id=plan.id,
                    action_id=action.id,
                    execution_id=execution.id,
                    event_type=RemediationAuditEventType.VERIFICATION_FAILED.value,
                    actor="recovery_verifier",
                    details={"reason": "telemetry_unavailable", "observations_count": len(v_record.observations)},
                )
                db.add(audit)

            else:
                # Still degraded or regression detected after timeout
                if final_status == VerificationStatus.VERIFYING:
                    final_status = VerificationStatus.VERIFICATION_FAILED
                v_status = VerificationStatus.VERIFICATION_FAILED.value
                v_record.status = v_status
                v_record.completed_at = datetime.now(UTC)
                v_record.summary = f"Recovery verification timed out. Telemetry did not achieve {required_healthy} consecutive healthy cycles."
                execution.status = ExecutionStatus.VERIFICATION_FAILED.value
                action.status = ActionStatus.FAILED.value
                plan.status = PlanStatus.FAILED.value

                audit = RemediationAuditEventModel(
                    incident_id=plan.incident_id,
                    investigation_id=plan.investigation_id,
                    plan_id=plan.id,
                    action_id=action.id,
                    execution_id=execution.id,
                    event_type=RemediationAuditEventType.VERIFICATION_FAILED.value,
                    actor="recovery_verifier",
                    details={"reason": "still_degraded_or_timed_out", "consecutive_healthy": consecutive_healthy},
                )
                db.add(audit)

            await db.commit()

        logger.info("Verification finished for execution %s with status: %s", execution_id, final_status)
        return final_status


# Global singleton verifier
recovery_verifier = RecoveryVerificationEngine()
