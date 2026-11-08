import asyncio
import os
import random
import time
from typing import Any

from fastapi import APIRouter, HTTPException, status

from common.schemas import FaultEnableRequest, FaultInfo

# Environment flag controlling whether fault injection endpoints are active
FAULT_INJECTION_ENV_KEY = "FAULT_INJECTION_ENABLED"


def is_fault_injection_globally_enabled() -> bool:
    """Return True if fault injection is permitted by environment configuration."""
    return os.getenv(FAULT_INJECTION_ENV_KEY, "false").lower() in ("true", "1", "yes")


class FaultState:
    """Represents a single registered fault configuration."""

    def __init__(self, name: str, description: str, default_params: dict[str, Any]) -> None:
        self.name = name
        self.description = description
        self.default_params = default_params
        self.enabled = False
        self.params: dict[str, Any] = dict(default_params)
        self.expires_at: float | None = None

    def enable(self, duration_seconds: int, params: dict[str, Any]) -> None:
        self.enabled = True
        self.params = {**self.default_params, **params}
        self.expires_at = time.time() + duration_seconds

    def disable(self) -> None:
        self.enabled = False
        self.params = dict(self.default_params)
        self.expires_at = None

    def is_active(self) -> bool:
        """Check if fault is enabled and within its bounded TTL window."""
        if not self.enabled:
            return False
        if self.expires_at is not None and time.time() > self.expires_at:
            self.disable()
            return False
        return True

    def get_info(self) -> FaultInfo:
        remaining = max(0.0, self.expires_at - time.time()) if self.expires_at else None
        return FaultInfo(
            name=self.name,
            description=self.description,
            enabled=self.is_active(),
            expires_in_seconds=round(remaining, 1) if remaining is not None else None,
            params=self.params,
        )


class FaultManager:
    """Central registry and evaluator for all controlled fault scenarios."""

    def __init__(self) -> None:
        self.faults: dict[str, FaultState] = {}
        self._register_default_faults()

    def _register_default_faults(self) -> None:
        self.register(
            "payment_failure_spike",
            "Simulates a sudden spike in payment declines",
            {"failure_rate": 0.8, "reason": "BANK_DECLINED_INSUFFICIENT_FUNDS"},
        )
        self.register(
            "downstream_latency_spike",
            "Injects bounded latency into downstream payment processing",
            {"delay_seconds": 2.5},
        )
        self.register(
            "payment_service_unavailability",
            "Simulates complete outage of the Payment Service returning HTTP 503",
            {"status_code": 503, "error_message": "Payment processing service unavailable"},
        )
        self.register(
            "db_pool_exhaustion",
            "Simulates database connection pool starvation by holding pool connections",
            {"hold_seconds": 10.0, "connections_to_hold": 7},
        )
        self.register(
            "cascading_timeout",
            "Simulates cascading downstream timeout exceeding caller deadline",
            {"delay_seconds": 5.0},
        )
        self.register(
            "order_service_error_spike",
            "Simulates internal pipeline failures and 500 errors in Order Service",
            {"error_rate": 0.7, "error_message": "Order processing pipeline internal error"},
        )

    def register(self, name: str, description: str, default_params: dict[str, Any]) -> None:
        self.faults[name] = FaultState(name, description, default_params)

    def enable(self, name: str, duration_seconds: int, params: dict[str, Any]) -> FaultInfo:
        if name not in self.faults:
            raise KeyError(f"Fault '{name}' not found")
        # Ensure bounded duration: maximum 600 seconds (10 minutes)
        safe_duration = min(max(duration_seconds, 1), 600)
        self.faults[name].enable(safe_duration, params)
        return self.faults[name].get_info()

    def disable(self, name: str) -> FaultInfo:
        if name not in self.faults:
            raise KeyError(f"Fault '{name}' not found")
        self.faults[name].disable()
        return self.faults[name].get_info()

    def reset_all(self) -> None:
        for fault in self.faults.values():
            fault.disable()

    def is_active(self, name: str) -> bool:
        if not is_fault_injection_globally_enabled():
            return False
        fault = self.faults.get(name)
        return fault.is_active() if fault else False

    def get_params(self, name: str) -> dict[str, Any]:
        fault = self.faults.get(name)
        return fault.params if fault else {}

    def list_all(self) -> list[FaultInfo]:
        return [f.get_info() for f in self.faults.values()]

    # Helper execution methods for services
    async def maybe_inject_latency(self, fault_name: str) -> None:
        if self.is_active(fault_name):
            delay = float(self.get_params(fault_name).get("delay_seconds", 1.0))
            # Safe upper bound
            delay = min(max(delay, 0.0), 10.0)
            await asyncio.sleep(delay)

    def should_trigger_probabilistic_fault(self, fault_name: str, rate_param: str = "rate") -> bool:
        if not self.is_active(fault_name):
            return False
        rate = float(self.get_params(fault_name).get(rate_param, 0.5))
        return random.random() < rate


# Singleton instance
fault_manager = FaultManager()


def create_fault_router() -> APIRouter:
    """Create the FastAPI router for controlling faults."""
    router = APIRouter(prefix="/debug/faults", tags=["Fault Injection"])

    def verify_enabled() -> None:
        if not is_fault_injection_globally_enabled():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Fault injection is disabled in this environment.",
            )

    @router.get("", response_model=list[FaultInfo])
    async def list_faults() -> list[FaultInfo]:
        """List all available fault scenarios and their current states."""
        verify_enabled()
        return fault_manager.list_all()

    @router.post("", response_model=FaultInfo)
    async def configure_fault(payload: dict[str, Any]) -> FaultInfo:
        """Configure and toggle a fault by payload (supports both enabled=True and False)."""
        verify_enabled()
        fault_name = payload.get("fault_type") or payload.get("name")
        if not fault_name:
            raise HTTPException(
                status_code=400, detail="Missing fault_type or name in request payload"
            )
        enabled = payload.get("enabled", True)
        if enabled:
            duration = int(payload.get("duration_seconds", 120))
            params = payload.get("parameters") or payload.get("params") or {}
            try:
                return fault_manager.enable(fault_name, duration, params)
            except KeyError as err:
                raise HTTPException(status_code=404, detail=str(err)) from err
        else:
            try:
                return fault_manager.disable(fault_name)
            except KeyError as err:
                raise HTTPException(status_code=404, detail=str(err)) from err

    @router.post("/{fault_name}/enable", response_model=FaultInfo)
    async def enable_fault(fault_name: str, request: FaultEnableRequest) -> FaultInfo:
        """Enable a specific fault scenario with optional parameters and TTL."""
        verify_enabled()
        try:
            return fault_manager.enable(fault_name, request.duration_seconds, request.params)
        except KeyError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err

    @router.post("/{fault_name}/disable", response_model=FaultInfo)
    async def disable_fault(fault_name: str) -> FaultInfo:
        """Disable a specific fault scenario immediately."""
        verify_enabled()
        try:
            return fault_manager.disable(fault_name)
        except KeyError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err

    @router.post("/reset", response_model=dict[str, str])
    async def reset_all_faults() -> dict[str, str]:
        """Reset and disable all fault scenarios."""
        verify_enabled()
        fault_manager.reset_all()
        return {"status": "all faults reset successfully"}

    return router
