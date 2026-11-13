"""Fixed, bounded scenarios for the isolated sample application."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    label: str
    service: str
    params: dict[str, float | int]
    signals: tuple[str, ...]


SCENARIOS = {
    "payment_failure_spike": Scenario(
        "Payment declines", "payment-service", {"failure_rate": 1.0}, ("payment_failure", "order_failure"),
    ),
    "downstream_latency_spike": Scenario(
        "Slow payments", "payment-service", {"delay_seconds": 2.5}, ("p95_latency",),
    ),
    "db_pool_exhaustion": Scenario(
        "Order database contention", "order-service", {"hold_seconds": 5},
        ("db_pool_saturation", "p95_latency"),
    ),
    "order_service_error_spike": Scenario(
        "Order service errors", "order-service", {"error_rate": 1.0}, ("http_5xx_rate",),
    ),
    "payment_service_unavailability": Scenario(
        "Payment endpoint unavailable", "payment-service", {"status_code": 503}, ("http_5xx_rate", "order_failure"),
    ),
    "cascading_timeout": Scenario(
        "Cascading timeouts", "payment-service", {"delay_seconds": 6}, ("p95_latency",),
    ),
}

DEMO_LEASE = "opspilot:demo:lease"
DEMO_STATUS = "opspilot:demo:status"
DEMO_RUNTIME = 210
DEMO_LEASE_SECONDS = DEMO_RUNTIME + 120


# Match the symptom on the affected service and verify the same signal after the action.
SCENARIO_RECOVERY = {
    "payment_failure_spike": ("payment_failure_ratio", 5.0),
    "downstream_latency_spike": ("p95_latency", 1.0),
    "db_pool_exhaustion": ("db_pool_saturation", 50.0),
    "order_service_error_spike": ("http_error_rate", 0.05),
    "payment_service_unavailability": ("http_error_rate", 0.05),
    "cascading_timeout": ("p95_latency", 1.0),
}
