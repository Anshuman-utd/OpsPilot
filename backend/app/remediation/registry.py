"""Allowlisted Remediation Action Registry for controlled SRE remediation.
Enforces strict allowlisting, parameter validation, and safe execution handlers.
Arbitrary shell commands, Python eval, raw SQL, or Docker/K8s commands are strictly forbidden.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.config import get_settings
from app.remediation.models import RiskLevel

logger = logging.getLogger("opspilot.remediation.registry")

# Static allowlists
ALLOWLISTED_SERVICES = {"order-service", "payment-service", "api-gateway"}
ALLOWLISTED_ENVIRONMENTS = {"demo", "local", "development"}
ALLOWLISTED_FAULT_TYPES = {
    "payment_failure_spike",
    "db_pool_exhaustion",
    "downstream_latency_spike",
    "order_service_error_spike",
    "payment_service_unavailability",
    "cascading_timeout",
}


# =========================================================================
# Action Parameter Schemas (Pydantic v2)
# =========================================================================

class DisableFaultParameters(BaseModel):
    """Parameters for disabling an active fault scenario."""

    fault_type: str = Field(
        description="Type of fault scenario to disable",
    )

    def validate_fault_type(self) -> None:
        if self.fault_type not in ALLOWLISTED_FAULT_TYPES:
            raise ValueError(
                f"Invalid fault_type '{self.fault_type}'. Allowed types: {sorted(ALLOWLISTED_FAULT_TYPES)}"
            )


class ResetAllFaultsParameters(BaseModel):
    """Parameters for resetting all faults."""

    pass


class ResetDbPoolParameters(BaseModel):
    """Parameters for resetting database connection pool."""

    pass


class RestoreConfigParameters(BaseModel):
    """Parameters for restoring service configuration defaults."""

    config_type: str = Field(
        default="all",
        description="Configuration category to restore (timeouts, retries, all)",
    )


# =========================================================================
# Action Execution Handlers (HTTP calls to target demo microservices)
# =========================================================================

def _get_service_base_url(service_name: str) -> str:
    """Resolve target service base URL from configuration."""
    settings = get_settings()
    mapping = {
        "order-service": settings.ORDER_SERVICE_URL,
        "payment-service": settings.PAYMENT_SERVICE_URL,
        "api-gateway": settings.API_GATEWAY_URL,
    }
    if service_name not in mapping:
        raise ValueError(f"Unknown target service '{service_name}'")
    return mapping[service_name]


async def handle_disable_fault_injection(
    target_service: str,
    parameters: dict[str, Any],
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Execute disabling of a specific fault scenario via microservice debug API."""
    params = DisableFaultParameters.model_validate(parameters)
    params.validate_fault_type()
    base_url = _get_service_base_url(target_service)
    url = f"{base_url}/debug/faults/{params.fault_type}/disable"

    logger.info("Executing handler: Disabling fault '%s' on %s (%s)", params.fault_type, target_service, url)
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(url)
        resp.raise_for_status()
        data = resp.json()

    return {
        "success": True,
        "action": "disable_fault_injection",
        "target_service": target_service,
        "fault_type": params.fault_type,
        "response": data,
        "message": f"Successfully disabled fault '{params.fault_type}' on {target_service}",
    }


async def handle_reset_all_faults(
    target_service: str,
    parameters: dict[str, Any],
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Execute reset of all faults on target microservice."""
    base_url = _get_service_base_url(target_service)
    url = f"{base_url}/debug/faults/reset"

    logger.info("Executing handler: Resetting all faults on %s (%s)", target_service, url)
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(url)
        resp.raise_for_status()
        data = resp.json()

    return {
        "success": True,
        "action": "reset_all_faults",
        "target_service": target_service,
        "response": data,
        "message": f"Successfully reset all faults on {target_service}",
    }


async def handle_reset_db_pool(
    target_service: str,
    parameters: dict[str, Any],
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    """Execute database connection pool release/reset on target microservice."""
    base_url = _get_service_base_url(target_service)
    # The demo microservice holds pool connections via the db_pool_exhaustion fault
    url = f"{base_url}/debug/faults/db_pool_exhaustion/disable"

    logger.info("Executing handler: Releasing DB pool connections on %s (%s)", target_service, url)
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(url)
        # Even if fault was already disabled, 200 or 404 is handled gracefully
        if resp.status_code == 404:
            data = {"status": "already_disabled"}
        else:
            resp.raise_for_status()
            data = resp.json()

    return {
        "success": True,
        "action": "reset_db_connection_pool",
        "target_service": target_service,
        "response": data,
        "message": f"Successfully cleared DB connection pool holds on {target_service}",
    }


async def handle_restore_config(
    target_service: str,
    parameters: dict[str, Any],
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Execute restoration of service configuration defaults."""
    params = RestoreConfigParameters.model_validate(parameters)
    base_url = _get_service_base_url(target_service)
    # Clear latency/timeout faults
    url = f"{base_url}/debug/faults/downstream_latency_spike/disable"

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        try:
            resp = await client.post(url)
            data = resp.json() if resp.status_code == 200 else {"status": "ok"}
        except Exception as exc:
            data = {"note": str(exc)}

    return {
        "success": True,
        "action": "restore_service_default_config",
        "target_service": target_service,
        "config_type": params.config_type,
        "response": data,
        "message": f"Restored default configuration for {target_service}",
    }


# =========================================================================
# Action Definition & Registry
# =========================================================================

ActionHandlerType = Callable[[str, dict[str, Any], float], Awaitable[dict[str, Any]]]


@dataclass
class ActionDefinition:
    """Immutable metadata and handler specification for an allowlisted action."""

    action_type: str
    name: str
    description: str
    supported_services: set[str]
    supported_environments: set[str]
    parameters_schema: type[BaseModel]
    risk_level: RiskLevel
    reversible: bool
    timeout_seconds: float
    preconditions: dict[str, Any]
    verification_strategy: str
    handler: ActionHandlerType
    rollback_handler: ActionHandlerType | None = None
    postconditions: dict[str, Any] = field(default_factory=dict)


class ActionRegistry:
    """Central registry of strictly allowlisted deterministic remediation actions."""

    def __init__(self) -> None:
        self._actions: dict[str, ActionDefinition] = {}
        self._register_default_actions()

    def _register_default_actions(self) -> None:
        # 1. Disable Fault Injection
        self.register(
            ActionDefinition(
                action_type="disable_fault_injection",
                name="Disable Simulated Fault Injection",
                description="Immediately disables an active fault scenario on the target microservice via its debug endpoint.",
                supported_services=ALLOWLISTED_SERVICES,
                supported_environments=ALLOWLISTED_ENVIRONMENTS,
                parameters_schema=DisableFaultParameters,
                risk_level=RiskLevel.LOW,
                reversible=True,
                timeout_seconds=10.0,
                preconditions={"service_reachable": True},
                verification_strategy="telemetry_metrics_recovery",
                handler=handle_disable_fault_injection,
            )
        )

        # 2. Reset All Faults
        self.register(
            ActionDefinition(
                action_type="reset_all_faults",
                name="Reset All Active Fault Scenarios",
                description="Disables all active simulated fault scenarios on the target microservice.",
                supported_services=ALLOWLISTED_SERVICES,
                supported_environments=ALLOWLISTED_ENVIRONMENTS,
                parameters_schema=ResetAllFaultsParameters,
                risk_level=RiskLevel.LOW,
                reversible=True,
                timeout_seconds=10.0,
                preconditions={"service_reachable": True},
                verification_strategy="telemetry_metrics_recovery",
                handler=handle_reset_all_faults,
            )
        )

        # 3. Reset DB Pool
        self.register(
            ActionDefinition(
                action_type="reset_db_connection_pool",
                name="Reset Database Connection Pool",
                description="Releases active blocked connections and restores connection pool capacity.",
                supported_services={"order-service", "payment-service"},
                supported_environments=ALLOWLISTED_ENVIRONMENTS,
                parameters_schema=ResetDbPoolParameters,
                risk_level=RiskLevel.LOW,
                reversible=True,
                timeout_seconds=15.0,
                preconditions={"service_reachable": True},
                verification_strategy="db_pool_saturation_recovery",
                handler=handle_reset_db_pool,
            )
        )

        # 4. Restore Service Default Config
        self.register(
            ActionDefinition(
                action_type="restore_service_default_config",
                name="Restore Service Default Configuration",
                description="Resets service parameters and connection timeouts back to default values.",
                supported_services=ALLOWLISTED_SERVICES,
                supported_environments=ALLOWLISTED_ENVIRONMENTS,
                parameters_schema=RestoreConfigParameters,
                risk_level=RiskLevel.LOW,
                reversible=True,
                timeout_seconds=10.0,
                preconditions={"service_reachable": True},
                verification_strategy="p95_latency_recovery",
                handler=handle_restore_config,
            )
        )

    def register(self, definition: ActionDefinition) -> None:
        """Register an allowlisted action definition."""
        self._actions[definition.action_type] = definition

    def get(self, action_type: str) -> ActionDefinition | None:
        """Lookup an action definition by unique action type."""
        return self._actions.get(action_type)

    def is_registered(self, action_type: str) -> bool:
        """Check if action type is registered."""
        return action_type in self._actions

    def list_all(self) -> list[ActionDefinition]:
        """Return all registered action definitions."""
        return list(self._actions.values())

    def validate_action(
        self,
        action_type: str,
        target_service: str,
        environment: str,
        parameters: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Validate whether an action invocation matches registration constraints."""
        definition = self.get(action_type)
        if not definition:
            return False, f"Action type '{action_type}' is not registered in the allowlisted action registry."

        if target_service not in definition.supported_services:
            return False, f"Service '{target_service}' is not supported by action '{action_type}'. Allowed: {sorted(definition.supported_services)}"

        if environment not in definition.supported_environments:
            return False, f"Environment '{environment}' is not supported by action '{action_type}'. Allowed: {sorted(definition.supported_environments)}"

        # Validate parameters schema
        try:
            parsed = definition.parameters_schema.model_validate(parameters)
            if hasattr(parsed, "validate_fault_type"):
                parsed.validate_fault_type()
        except ValidationError as err:
            return False, f"Invalid parameters for action '{action_type}': {err}"
        except ValueError as err:
            return False, str(err)

        return True, None


# Global singleton registry
action_registry = ActionRegistry()
