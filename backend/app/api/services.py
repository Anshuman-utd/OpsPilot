"""Authenticated access to the demo service catalog and optional test orders."""

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.health import ReadinessResponse, readiness_check
from app.core.config import get_settings

router = APIRouter(prefix="/api/v1/services", tags=["Services"])


@router.get("/platform", response_model=ReadinessResponse)
async def platform_health(response: Response) -> ReadinessResponse:
    return await readiness_check(response)


class DemoOrder(BaseModel):
    customer_id: str = Field(min_length=1, max_length=64)
    amount: float = Field(gt=0, le=10000, allow_inf_nan=False)
    currency: str = Field(pattern="^[A-Z]{3}$")


async def gateway_request(method: str, path: str, payload: dict | None = None) -> Any:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
            response = await client.request(method, f"{settings.API_GATEWAY_URL}{path}", json=payload)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="The demo gateway could not complete the request.") from exc


@router.get("/health")
async def service_health() -> Any:
    result = await gateway_request("GET", "/services/health")
    return {
        "status": result["status"],
        "timestamp": result["timestamp"],
        "services": {name: {"status": item["status"]} for name, item in result["services"].items()},
    }


@router.post("/demo-orders", status_code=201)
async def create_demo_order(order: DemoOrder) -> Any:
    if not get_settings().DEMO_ORDERS_ENABLED:
        raise HTTPException(status_code=403, detail="Demo order creation is disabled.")
    return await gateway_request("POST", "/api/orders", order.model_dump())
