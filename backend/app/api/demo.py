"""Operator controls for the isolated demo lab."""

import json
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db_session
from app.core.redis import get_redis_client
from app.models.incident import IncidentModel
from app.services.demo import DEMO_LEASE, DEMO_LEASE_SECONDS, DEMO_STATUS, SCENARIOS
from app.workers.demo_tasks import run_demo_scenario

router = APIRouter(prefix="/api/v1/demo", tags=["Demo"])


@router.get("")
async def get_demo(db: Annotated[AsyncSession, Depends(get_db_session)]) -> dict:
    if not get_settings().demo_lab_enabled:
        return {"enabled": False, "scenarios": [], "run": None}
    redis = get_redis_client()
    try:
        saved, lease = await redis.mget(DEMO_STATUS, DEMO_LEASE)
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Demo status is temporarily unavailable.") from exc
    run = json.loads(saved) if saved else None
    if run and not lease and run["status"] in {"queued", "warming", "running"}:
        run["status"] = "interrupted"
    if run and run.get("fault_started_at") and (scenario := SCENARIOS.get(run["scenario"])):
        started = datetime.fromtimestamp(run["fault_started_at"], tz=UTC)
        incident = await db.scalar(
            select(IncidentModel)
            .where(
                IncidentModel.service_name == scenario.service,
                IncidentModel.incident_type.in_(scenario.signals),
                IncidentModel.triggered_at >= started,
            )
            .order_by(IncidentModel.triggered_at.asc(), IncidentModel.id.asc())
            .limit(1)
        )
        if incident:
            run["incident"] = {"id": incident.id, "title": incident.title, "status": incident.status}
    return {
        "enabled": True,
        "scenarios": [
            {"id": key, "label": value.label, "service": value.service, "signals": value.signals}
            for key, value in SCENARIOS.items()
        ],
        "run": run,
    }


@router.post("/{scenario_id}", status_code=202)
async def start_demo(scenario_id: str) -> dict:
    if not get_settings().demo_lab_enabled:
        raise HTTPException(status_code=403, detail="The demo lab is disabled.")
    if scenario_id not in SCENARIOS:
        raise HTTPException(status_code=404, detail="Unknown demo scenario.")
    redis = get_redis_client()
    job = uuid4().hex
    run = {"job": job, "scenario": scenario_id, "status": "queued", "orders_sent": 0}
    try:
        # The lock protects runs from overlapping. Publish status before dispatch.
        if not await redis.set(DEMO_LEASE, job, nx=True, ex=DEMO_LEASE_SECONDS):
            raise HTTPException(status_code=409, detail="A demo run is already in progress.")
        await redis.set(DEMO_STATUS, json.dumps(run), ex=600)
        run_demo_scenario.apply_async(args=[job, scenario_id], retry=False, expires=60)
    except HTTPException:
        raise
    except Exception as exc:
        # A broker failure can be ambiguous. Keep the reservation until it expires.
        raise HTTPException(status_code=503, detail="Could not queue the demo run. Check the worker and try again shortly.") from exc
    return run
