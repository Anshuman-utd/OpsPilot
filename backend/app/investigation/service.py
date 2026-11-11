import logging
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.investigation.models import (
    InvestigationAgentRunModel,
    InvestigationModel,
    InvestigationStatus,
)
from app.investigation.providers import get_llm_provider
from app.investigation.schemas import InvestigationCreateRequest
from app.models.incident import IncidentModel, IncidentStatus

logger = logging.getLogger("opspilot.investigation.service")


class InvestigationService:
    """Service handling autonomous investigation orchestration, persistence, and state transitions."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def create_investigation(
        self,
        incident_id: str,
        session: AsyncSession,
        request: InvestigationCreateRequest | None = None,
    ) -> InvestigationModel:
        """
        Create and queue a new investigation for an incident.
        Validates incident existence, LLM provider availability, and concurrency constraints.
        """
        request = request or InvestigationCreateRequest()

        # 1. Verify incident exists
        stmt = select(IncidentModel).where(IncidentModel.id == incident_id)
        incident = (await session.execute(stmt)).scalar_one_or_none()
        if not incident:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Incident '{incident_id}' not found.",
            )

        # 2. Check if autonomous investigation is enabled globally
        if not self.settings.INVESTIGATION_ENABLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Autonomous incident investigation is currently disabled in system configuration.",
            )

        # 3. Check LLM provider credentials
        provider = get_llm_provider(self.settings)
        if not provider.is_configured():
            logger.warning(
                "Investigation rejected: LLM provider '%s' has no API key.",
                provider.get_provider_name(),
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"LLM provider '{provider.get_provider_name()}' is not configured. "
                    "Please set GROQ_API_KEY in environment."
                ),
            )

        # 4. Check for active running investigation on this incident to prevent duplicate overlapping runs
        running_stmt = select(InvestigationModel).where(
            InvestigationModel.incident_id == incident_id,
            InvestigationModel.status.in_(
                [
                    InvestigationStatus.PENDING.value,
                    InvestigationStatus.RUNNING.value,
                ]
            ),
        )
        existing_active = (await session.execute(running_stmt)).scalar_one_or_none()
        if existing_active:
            logger.info(
                "Active investigation %s already exists for incident %s",
                existing_active.id,
                incident_id,
            )
            return existing_active

        # 5. Create new investigation record
        now = datetime.now(UTC)
        model_name = request.model_override or provider.model_name
        investigation = InvestigationModel(
            incident_id=incident_id,
            status=InvestigationStatus.PENDING.value,
            trigger_type=request.trigger_type.value,
            model_provider=provider.get_provider_name(),
            model_name=model_name,
            created_at=now,
            updated_at=now,
        )
        session.add(investigation)

        # Automatically transition incident status to INVESTIGATING if currently OPEN or ACKNOWLEDGED
        if incident.status in [IncidentStatus.OPEN.value, IncidentStatus.ACKNOWLEDGED.value]:
            incident.status = IncidentStatus.INVESTIGATING.value
            incident.updated_at = now

        await session.commit()
        await session.refresh(investigation)

        logger.info(
            "Created investigation %s for incident %s (model: %s)",
            investigation.id,
            incident_id,
            model_name,
        )

        # 6. Dispatch background worker task (handled via celery or test runner)
        try:
            from app.workers.investigation_tasks import run_investigation_task

            run_investigation_task.delay(investigation.id)
        except Exception as exc:
            logger.warning(
                "Could not dispatch Celery task immediately (broker offline or testing): %s", exc
            )

        return investigation

    async def list_investigations_for_incident(
        self,
        incident_id: str,
        session: AsyncSession,
    ) -> list[InvestigationModel]:
        """List chronological investigation history for an incident."""
        stmt = (
            select(InvestigationModel)
            .where(InvestigationModel.incident_id == incident_id)
            .order_by(InvestigationModel.created_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_investigation_detail(
        self,
        investigation_id: str,
        session: AsyncSession,
    ) -> InvestigationModel:
        """Retrieve full investigation detail including agent runs and final report."""
        stmt = (
            select(InvestigationModel)
            .options(selectinload(InvestigationModel.agent_runs))
            .where(InvestigationModel.id == investigation_id)
        )
        investigation = (await session.execute(stmt)).scalar_one_or_none()
        if not investigation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Investigation '{investigation_id}' not found.",
            )
        return investigation

    async def get_agent_runs(
        self,
        investigation_id: str,
        session: AsyncSession,
    ) -> list[InvestigationAgentRunModel]:
        """Retrieve all agent runs for an investigation."""
        stmt = (
            select(InvestigationAgentRunModel)
            .where(InvestigationAgentRunModel.investigation_id == investigation_id)
            .order_by(InvestigationAgentRunModel.created_at.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def retry_investigation(
        self,
        investigation_id: str,
        session: AsyncSession,
    ) -> InvestigationModel:
        """Retry a failed or cancelled investigation."""
        stmt = select(InvestigationModel).where(InvestigationModel.id == investigation_id)
        investigation = (await session.execute(stmt)).scalar_one_or_none()
        if not investigation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Investigation '{investigation_id}' not found.",
            )

        if investigation.status in [
            InvestigationStatus.RUNNING.value,
            InvestigationStatus.PENDING.value,
        ]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Investigation is already running or pending.",
            )

        # Reset state
        now = datetime.now(UTC)
        investigation.status = InvestigationStatus.PENDING.value
        investigation.error_message = None
        investigation.started_at = None
        investigation.completed_at = None
        investigation.updated_at = now
        await session.commit()
        await session.refresh(investigation)

        try:
            from app.workers.investigation_tasks import run_investigation_task

            run_investigation_task.delay(investigation.id)
        except Exception as exc:
            logger.warning("Could not dispatch Celery retry task: %s", exc)

        return investigation
