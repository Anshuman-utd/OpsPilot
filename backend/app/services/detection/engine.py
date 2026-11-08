import logging
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.incident import (
    IncidentEventModel,
    IncidentEventType,
    IncidentModel,
    IncidentStatus,
)
from app.schemas.detection import DetectionRunResponse
from app.services.detection.rules import BaseDetectionRule, RuleBreach, get_default_rules
from app.services.prometheus import PrometheusClient

logger = logging.getLogger("opspilot.detection.engine")


class DetectionEngine:
    """Deterministic rule-based SRE incident detection and deduplication engine."""

    def __init__(
        self,
        rules: list[BaseDetectionRule] | None = None,
        prometheus_client: PrometheusClient | None = None,
    ) -> None:
        self.rules = rules or get_default_rules()
        self.prometheus_client = prometheus_client or PrometheusClient()

    async def evaluate_all(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
        auto_resolve: bool = True,
    ) -> DetectionRunResponse:
        """
        Execute an evaluation pass across all configured rules against Prometheus.
        Creates new incidents, deduplicates active incidents, and auto-resolves recovered services.
        """
        settings = settings or get_settings()
        start_time = time.perf_counter()

        rules_evaluated = 0
        total_breaches = 0
        incidents_created = 0
        incidents_updated = 0
        incidents_resolved = 0
        details: list[dict[str, Any]] = []

        all_breaches: list[RuleBreach] = []
        healthy_fingerprints_set: set[str] = set()

        for rule in self.rules:
            if not rule.enabled:
                continue

            rules_evaluated += 1
            try:
                breaches, healthy_fps = await rule.evaluate(self.prometheus_client, settings)
                all_breaches.extend(breaches)
                healthy_fingerprints_set.update(healthy_fps)
            except Exception as exc:
                logger.exception("Error evaluating rule %s: %s", rule.rule_id, exc)

        total_breaches = len(all_breaches)

        active_statuses = [
            IncidentStatus.OPEN.value,
            IncidentStatus.ACKNOWLEDGED.value,
            IncidentStatus.INVESTIGATING.value,
        ]

        breached_fingerprints = {b.fingerprint for b in all_breaches}

        for breach in all_breaches:
            stmt = select(IncidentModel).where(
                IncidentModel.fingerprint == breach.fingerprint,
                IncidentModel.status.in_(active_statuses),
            )
            result = await session.execute(stmt)
            existing_incident = result.scalar_one_or_none()

            now = datetime.now(UTC)

            if existing_incident:
                existing_incident.occurrence_count += 1
                existing_incident.last_seen_at = now
                existing_incident.triggered_value = breach.observed_value
                existing_incident.threshold_value = breach.threshold_value
                existing_incident.evidence = breach.evidence

                if breach.severity.value == "CRITICAL" and existing_incident.severity != "CRITICAL":
                    old_sev = existing_incident.severity
                    existing_incident.severity = "CRITICAL"
                    session.add(
                        IncidentEventModel(
                            incident_id=existing_incident.id,
                            event_type=IncidentEventType.INCIDENT_UPDATED.value,
                            from_status=existing_incident.status,
                            to_status=existing_incident.status,
                            message=(
                                f"Severity escalated from {old_sev} to CRITICAL "
                                f"(observed: {breach.observed_value})"
                            ),
                            actor="detection_engine",
                            payload=breach.evidence,
                            created_at=now,
                        )
                    )
                else:
                    session.add(
                        IncidentEventModel(
                            incident_id=existing_incident.id,
                            event_type=IncidentEventType.INCIDENT_UPDATED.value,
                            from_status=existing_incident.status,
                            to_status=existing_incident.status,
                            message=(
                                f"Threshold breach re-observed: value={breach.observed_value} "
                                f"(count={existing_incident.occurrence_count})"
                            ),
                            actor="detection_engine",
                            payload=breach.evidence,
                            created_at=now,
                        )
                    )

                incidents_updated += 1
                details.append({
                    "action": "updated",
                    "incident_id": existing_incident.id,
                    "title": existing_incident.title,
                    "service": existing_incident.service_name,
                    "occurrence_count": existing_incident.occurrence_count,
                    "observed_value": breach.observed_value,
                })

            else:
                new_incident = IncidentModel(
                    title=breach.title,
                    description=breach.description,
                    service_name=breach.service_name,
                    severity=breach.severity.value,
                    status=IncidentStatus.OPEN.value,
                    incident_type=breach.incident_type,
                    source="detection_engine",
                    fingerprint=breach.fingerprint,
                    triggered_value=breach.observed_value,
                    threshold_value=breach.threshold_value,
                    evidence=breach.evidence,
                    occurrence_count=1,
                    triggered_at=breach.detected_at,
                    last_seen_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(new_incident)
                await session.flush()

                session.add(
                    IncidentEventModel(
                        incident_id=new_incident.id,
                        event_type=IncidentEventType.INCIDENT_CREATED.value,
                        from_status=None,
                        to_status=IncidentStatus.OPEN.value,
                        message=f"Incident opened: {breach.title}",
                        actor="detection_engine",
                        payload=breach.evidence,
                        created_at=now,
                    )
                )

                incidents_created += 1
                details.append({
                    "action": "created",
                    "incident_id": new_incident.id,
                    "title": new_incident.title,
                    "service": new_incident.service_name,
                    "severity": new_incident.severity,
                    "observed_value": breach.observed_value,
                })

        if auto_resolve:
            recoverable_fps = healthy_fingerprints_set - breached_fingerprints

            if recoverable_fps:
                stmt = select(IncidentModel).where(
                    IncidentModel.fingerprint.in_(recoverable_fps),
                    IncidentModel.status.in_(active_statuses),
                )
                res = await session.execute(stmt)
                recoverable_incidents = res.scalars().all()

                now = datetime.now(UTC)
                for inc in recoverable_incidents:
                    from_status = inc.status
                    inc.status = IncidentStatus.RESOLVED.value
                    inc.resolved_at = now
                    inc.updated_at = now

                    session.add(
                        IncidentEventModel(
                            incident_id=inc.id,
                            event_type=IncidentEventType.INCIDENT_RESOLVED.value,
                            from_status=from_status,
                            to_status=IncidentStatus.RESOLVED.value,
                            message="Incident auto-resolved: metric returned below threshold.",
                            actor="detection_engine",
                            payload={"resolved_at": now.isoformat()},
                            created_at=now,
                        )
                    )
                    incidents_resolved += 1
                    details.append({
                        "action": "resolved",
                        "incident_id": inc.id,
                        "title": inc.title,
                        "service": inc.service_name,
                    })

        await session.commit()

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.info(
            "Detection pass completed in %.1fms: %d rules, %d breaches, %d created, %d updated",
            duration_ms,
            rules_evaluated,
            total_breaches,
            incidents_created,
            incidents_updated,
        )

        return DetectionRunResponse(
            status="success",
            rules_evaluated=rules_evaluated,
            breaches_detected=total_breaches,
            incidents_created=incidents_created,
            incidents_updated=incidents_updated,
            incidents_resolved=incidents_resolved,
            evaluation_duration_ms=duration_ms,
            details=details,
        )
