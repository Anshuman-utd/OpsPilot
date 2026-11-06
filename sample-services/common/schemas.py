from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class OrderCreateRequest(BaseModel):
    customer_id: str = Field(..., min_length=1, max_length=100, description="Customer Identifier")
    amount: float = Field(..., gt=0, description="Order total amount in major currency units")
    currency: str = Field(
        default="USD", min_length=3, max_length=3, description="ISO 4217 currency code"
    )


class OrderResponse(BaseModel):
    id: str
    customer_id: str
    amount: float
    currency: str
    status: str
    created_at: datetime
    updated_at: datetime
    payment_id: str | None = None


class PaymentCreateRequest(BaseModel):
    order_id: str = Field(..., min_length=1, max_length=36, description="Associated Order ID")
    amount: float = Field(..., gt=0, description="Amount to charge")
    currency: str = Field(default="USD", min_length=3, max_length=3)


class PaymentResponse(BaseModel):
    id: str
    order_id: str
    amount: float
    currency: str
    status: str
    failure_reason: str | None = None
    created_at: datetime


class ServiceHealthResponse(BaseModel):
    service: str
    status: str
    timestamp: str
    database: str | None = None
    dependencies: dict[str, Any] | None = None


class FaultEnableRequest(BaseModel):
    duration_seconds: int = Field(
        default=120, ge=1, le=600, description="Maximum TTL for fault before auto-reset"
    )
    params: dict[str, Any] = Field(default_factory=dict, description="Fault-specific parameters")


class FaultInfo(BaseModel):
    name: str
    description: str
    enabled: bool
    expires_in_seconds: float | None = None
    params: dict[str, Any] = Field(default_factory=dict)
