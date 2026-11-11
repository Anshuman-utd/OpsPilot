"""Incident history tools for correlating past failures and RCA knowledge."""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.investigation.models import InvestigationStatus
from app.models.incident import IncidentModel

logger = logging.getLogger("opspilot.investigation.tools.incident")


@dataclass
class HistoricalIncidentSummary:
    incident_id: str
    title: str
    service_name: str
    severity: str
    status: str
    triggered_at: str
    resolved_at: str | None
    duration_minutes: float | None
    incident_type: str
    previous_rca_summary: str | None = None


@dataclass
class HistoricalSearchResult:
    tool_name: str
    query_target: str
    success: bool
    incidents: list[HistoricalIncidentSummary] = field(default_factory=list)
    total_found: int = 0
    summary_text: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "query_target": self.query_target,
            "success": self.success,
            "total_found": self.total_found,
            "summary_text": self.summary_text,
            "error": self.error,
            "incidents": [
                {
                    "incident_id": inc.incident_id,
                    "title": inc.title,
                    "service_name": inc.service_name,
                    "severity": inc.severity,
                    "status": inc.status,
                    "triggered_at": inc.triggered_at,
                    "resolved_at": inc.resolved_at,
                    "duration_minutes": round(inc.duration_minutes, 1)
                    if inc.duration_minutes
                    else None,
                    "incident_type": inc.incident_type,
                    "previous_rca_summary": inc.previous_rca_summary,
                }
                for inc in self.incidents
            ],
        }


class IncidentHistoryTool:
    """Tool for retrieving historical incident occurrences and previous root-cause analyses."""

    async def get_historical_incidents(
        self,
        session: AsyncSession,
        service_name: str,
        lookback_days: int = 14,
        limit: int = 5,
        exclude_incident_id: str | None = None,
    ) -> HistoricalSearchResult:
        """Find past resolved or closed incidents for the same service."""
        try:
            since = datetime.now(UTC) - timedelta(days=lookback_days)
            stmt = (
                select(IncidentModel)
                .options(selectinload(IncidentModel.investigations))
                .where(
                    IncidentModel.service_name == service_name,
                    IncidentModel.triggered_at >= since,
                )
                .order_by(desc(IncidentModel.triggered_at))
                .limit(limit + 1)
            )

            result = await session.execute(stmt)
            raw_incidents = result.scalars().all()

            summaries: list[HistoricalIncidentSummary] = []
            for inc in raw_incidents:
                if exclude_incident_id and str(inc.id) == str(exclude_incident_id):
                    continue

                duration_min = None
                if inc.resolved_at and inc.triggered_at:
                    duration_min = (inc.resolved_at - inc.triggered_at).total_seconds() / 60.0

                # Extract latest successful RCA summary if exists
                prev_rca = None
                if inc.investigations:
                    for inv in sorted(inc.investigations, key=lambda x: x.created_at, reverse=True):
                        if inv.status == InvestigationStatus.COMPLETED and inv.summary:
                            prev_rca = inv.summary
                            break

                summaries.append(
                    HistoricalIncidentSummary(
                        incident_id=str(inc.id),
                        title=inc.title,
                        service_name=inc.service_name,
                        severity=inc.severity,
                        status=inc.status,
                        triggered_at=inc.triggered_at.isoformat(),
                        resolved_at=inc.resolved_at.isoformat() if inc.resolved_at else None,
                        duration_minutes=duration_min,
                        incident_type=inc.incident_type or "unknown",
                        previous_rca_summary=prev_rca,
                    )
                )

                if len(summaries) >= limit:
                    break

            summary = (
                f"Found {len(summaries)} past incident(s) for service '{service_name}' "
                f"in the last {lookback_days} days."
            )

            return HistoricalSearchResult(
                tool_name="get_historical_incidents",
                query_target=service_name,
                success=True,
                incidents=summaries,
                total_found=len(summaries),
                summary_text=summary,
            )

        except Exception as exc:
            logger.error("Error retrieving historical incidents for %s: %s", service_name, exc)
            return HistoricalSearchResult(
                tool_name="get_historical_incidents",
                query_target=service_name,
                success=False,
                error=str(exc),
            )

    async def get_similar_incidents_by_fingerprint(
        self,
        session: AsyncSession,
        fingerprint: str,
        limit: int = 5,
        exclude_incident_id: str | None = None,
    ) -> HistoricalSearchResult:
        """Find recurring incidents with identical deterministic fingerprint."""
        try:
            stmt = (
                select(IncidentModel)
                .options(selectinload(IncidentModel.investigations))
                .where(IncidentModel.fingerprint == fingerprint)
                .order_by(desc(IncidentModel.triggered_at))
                .limit(limit + 1)
            )

            result = await session.execute(stmt)
            raw_incidents = result.scalars().all()

            summaries: list[HistoricalIncidentSummary] = []
            for inc in raw_incidents:
                if exclude_incident_id and str(inc.id) == str(exclude_incident_id):
                    continue

                duration_min = None
                if inc.resolved_at and inc.triggered_at:
                    duration_min = (inc.resolved_at - inc.triggered_at).total_seconds() / 60.0

                prev_rca = None
                if inc.investigations:
                    for inv in sorted(inc.investigations, key=lambda x: x.created_at, reverse=True):
                        if inv.status == InvestigationStatus.COMPLETED and inv.summary:
                            prev_rca = inv.summary
                            break

                summaries.append(
                    HistoricalIncidentSummary(
                        incident_id=str(inc.id),
                        title=inc.title,
                        service_name=inc.service_name,
                        severity=inc.severity,
                        status=inc.status,
                        triggered_at=inc.triggered_at.isoformat(),
                        resolved_at=inc.resolved_at.isoformat() if inc.resolved_at else None,
                        duration_minutes=duration_min,
                        incident_type=inc.incident_type or "unknown",
                        previous_rca_summary=prev_rca,
                    )
                )

                if len(summaries) >= limit:
                    break

            summary = f"Found {len(summaries)} recurring incident(s) matching fingerprint '{fingerprint}'."

            return HistoricalSearchResult(
                tool_name="get_similar_incidents_by_fingerprint",
                query_target=fingerprint,
                success=True,
                incidents=summaries,
                total_found=len(summaries),
                summary_text=summary,
            )

        except Exception as exc:
            logger.error(
                "Error retrieving similar incidents for fingerprint %s: %s", fingerprint, exc
            )
            return HistoricalSearchResult(
                tool_name="get_similar_incidents_by_fingerprint",
                query_target=fingerprint,
                success=False,
                error=str(exc),
            )
