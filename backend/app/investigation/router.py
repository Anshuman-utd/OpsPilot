import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.investigation.schemas import (
    InvestigationAgentRunResponse,
    InvestigationCreateRequest,
    InvestigationDetailResponse,
    InvestigationResponse,
)
from app.investigation.service import InvestigationService

logger = logging.getLogger("opspilot.api.investigations")

router = APIRouter(tags=["Investigations"])
service = InvestigationService()


@router.post(
    "/api/v1/incidents/{incident_id}/investigations",
    response_model=InvestigationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_incident_investigation(
    incident_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    request: InvestigationCreateRequest | None = None,
) -> InvestigationResponse:
    """Initiate an autonomous multi-agent RCA investigation for an incident."""
    investigation = await service.create_investigation(incident_id, db, request)
    return InvestigationResponse.model_validate(investigation)


@router.get(
    "/api/v1/incidents/{incident_id}/investigations",
    response_model=list[InvestigationResponse],
)
async def list_incident_investigations(
    incident_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[InvestigationResponse]:
    """Retrieve chronological investigation history for a specific incident."""
    investigations = await service.list_investigations_for_incident(incident_id, db)
    return [InvestigationResponse.model_validate(inv) for inv in investigations]


@router.get(
    "/api/v1/investigations/{investigation_id}",
    response_model=InvestigationDetailResponse,
)
async def get_investigation_detail(
    investigation_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> InvestigationDetailResponse:
    """Retrieve full details of an investigation including synthesized RCA report and agent runs."""
    investigation = await service.get_investigation_detail(investigation_id, db)
    return InvestigationDetailResponse.model_validate(investigation)


@router.get(
    "/api/v1/investigations/{investigation_id}/agents",
    response_model=list[InvestigationAgentRunResponse],
)
async def get_investigation_agent_runs(
    investigation_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[InvestigationAgentRunResponse]:
    """Inspect real-time execution status and findings for each specialized agent in an investigation."""
    runs = await service.get_agent_runs(investigation_id, db)
    return [InvestigationAgentRunResponse.model_validate(r) for r in runs]


@router.post(
    "/api/v1/investigations/{investigation_id}/retry",
    response_model=InvestigationResponse,
)
async def retry_failed_investigation(
    investigation_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> InvestigationResponse:
    """Retry a failed or interrupted investigation."""
    investigation = await service.retry_investigation(investigation_id, db)
    return InvestigationResponse.model_validate(investigation)

