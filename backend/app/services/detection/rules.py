import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.models.incident import IncidentSeverity
from app.services.prometheus import PrometheusClient

logger = logging.getLogger("opspilot.detection.rules")


@dataclass
class RuleBreach:
    """Represents an observed metric breach triggering or escalating an incident."""

    rule_id: str
    rule_name: str
    service_name: str
    severity: IncidentSeverity
    observed_value: float
    threshold_value: float
    title: str
    description: str
    incident_type: str
    fingerprint: str
    evidence: dict[str, Any]
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class BaseDetectionRule(ABC):
    """Abstract base class for all deterministic SRE detection rules."""

    def __init__(self, rule_id: str, name: str, description: str, enabled: bool = True) -> None:
        self.rule_id = rule_id
        self.name = name
        self.description = description
        self.enabled = enabled

    @abstractmethod
    async def evaluate(
        self, client: PrometheusClient, settings: Settings
    ) -> tuple[list[RuleBreach], list[str]]:
        """
        Evaluate the rule against Prometheus.
        Returns:
            tuple of (breaches: list[RuleBreach], healthy_fingerprints: list[str])
        """
        ...

    def generate_fingerprint(self, service_name: str, discriminator: str = "") -> str:
        """
        Generate a deterministic, stable fingerprint for incident deduplication.
        Fingerprint must depend strictly on immutable identity (rule_id, service_name, entity),
        NEVER on changing timestamps or observed values.
        """
        raw = f"{self.rule_id}:{service_name}:{discriminator}".strip(":")
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class HighHttp5xxErrorRateRule(BaseDetectionRule):
    """Detects elevated ratio of HTTP 5xx server errors relative to total requests."""

    def __init__(self, enabled: bool = True) -> None:
        super().__init__(
            rule_id="high_http_5xx_rate",
            name="High HTTP 5xx Error Rate",
            description="HTTP 5xx server error rate exceeds acceptable threshold over 5m window",
            enabled=enabled,
        )

    async def evaluate(
        self, client: PrometheusClient, settings: Settings
    ) -> tuple[list[RuleBreach], list[str]]:
        if not self.enabled:
            return [], []

        query = (
            'sum by (service) (rate(http_requests_total{status_code=~"5.."}[5m])) '
            "/ clamp_min(sum by (service) (rate(http_requests_total[5m])), 0.001)"
        )
        volume_query = "sum by (service) (rate(http_requests_total[5m]))"

        ratio_result = await client.query_instant(query)
        if not ratio_result.success:
            return [], []

        volume_result = await client.query_instant(volume_query)
        volume_map: dict[str, float] = {}
        if volume_result.success:
            for s in volume_result.samples:
                svc = s.labels.get("service", "unknown")
                volume_map[svc] = s.value

        breaches: list[RuleBreach] = []
        healthy_fingerprints: list[str] = []

        crit_t = settings.HTTP_5XX_CRITICAL_THRESHOLD
        high_t = settings.HTTP_5XX_HIGH_THRESHOLD

        for sample in ratio_result.samples:
            svc = sample.labels.get("service")
            if not svc or svc == "prometheus":
                continue

            rate_ratio = sample.value
            total_req_rate = volume_map.get(svc, 0.0)
            fingerprint = self.generate_fingerprint(svc)

            if total_req_rate < 0.05:
                continue

            observed_pct = round(rate_ratio * 100, 2)

            if rate_ratio >= crit_t:
                breaches.append(
                    RuleBreach(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        service_name=svc,
                        severity=IncidentSeverity.CRITICAL,
                        observed_value=observed_pct,
                        threshold_value=round(crit_t * 100, 2),
                        title=f"Critical HTTP 5xx error rate on {svc} ({observed_pct}%)",
                        description=(
                            f"Service {svc} error rate reached {observed_pct}% "
                            f"(critical threshold: {round(crit_t * 100, 2)}%)."
                        ),
                        incident_type="http_5xx_rate",
                        fingerprint=fingerprint,
                        evidence={
                            "rule_id": self.rule_id,
                            "promql": query,
                            "observed_error_ratio": round(rate_ratio, 4),
                            "observed_percentage": f"{observed_pct}%",
                            "threshold_percentage": f"{round(crit_t * 100, 2)}%",
                            "request_rate_per_sec": round(total_req_rate, 3),
                            "window": "5m",
                            "service": svc,
                        },
                    )
                )
            elif rate_ratio >= high_t:
                breaches.append(
                    RuleBreach(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        service_name=svc,
                        severity=IncidentSeverity.HIGH,
                        observed_value=observed_pct,
                        threshold_value=round(high_t * 100, 2),
                        title=f"Elevated HTTP 5xx error rate on {svc} ({observed_pct}%)",
                        description=(
                            f"Service {svc} error rate breached threshold at {observed_pct}% "
                            f"(high threshold: {round(high_t * 100, 2)}%)."
                        ),
                        incident_type="http_5xx_rate",
                        fingerprint=fingerprint,
                        evidence={
                            "rule_id": self.rule_id,
                            "promql": query,
                            "observed_error_ratio": round(rate_ratio, 4),
                            "observed_percentage": f"{observed_pct}%",
                            "threshold_percentage": f"{round(high_t * 100, 2)}%",
                            "request_rate_per_sec": round(total_req_rate, 3),
                            "window": "5m",
                            "service": svc,
                        },
                    )
                )
            else:
                healthy_fingerprints.append(fingerprint)

        return breaches, healthy_fingerprints


class HighP95LatencyRule(BaseDetectionRule):
    """Detects elevated 95th percentile HTTP request latency."""

    def __init__(self, enabled: bool = True) -> None:
        super().__init__(
            rule_id="high_p95_latency",
            name="High P95 Latency",
            description="P95 HTTP response latency exceeds SLA target over 5m window",
            enabled=enabled,
        )

    async def evaluate(
        self, client: PrometheusClient, settings: Settings
    ) -> tuple[list[RuleBreach], list[str]]:
        if not self.enabled:
            return [], []

        query = (
            "histogram_quantile(0.95, sum by (le, service) "
            "(rate(http_request_duration_seconds_bucket[5m])))"
        )
        result = await client.query_instant(query)
        if not result.success:
            return [], []

        breaches: list[RuleBreach] = []
        healthy_fingerprints: list[str] = []

        crit_t = settings.P95_LATENCY_CRITICAL_SECONDS
        high_t = settings.P95_LATENCY_HIGH_SECONDS

        for sample in result.samples:
            svc = sample.labels.get("service")
            if not svc or svc == "prometheus":
                continue

            p95_sec = sample.value
            fingerprint = self.generate_fingerprint(svc)

            if p95_sec >= crit_t:
                breaches.append(
                    RuleBreach(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        service_name=svc,
                        severity=IncidentSeverity.CRITICAL,
                        observed_value=round(p95_sec, 3),
                        threshold_value=round(crit_t, 3),
                        title=f"Critical P95 latency on {svc} ({round(p95_sec, 2)}s)",
                        description=(
                            f"Service {svc} p95 latency reached {round(p95_sec, 2)}s "
                            f"(critical threshold: {crit_t}s)."
                        ),
                        incident_type="p95_latency",
                        fingerprint=fingerprint,
                        evidence={
                            "rule_id": self.rule_id,
                            "promql": query,
                            "observed_p95_seconds": round(p95_sec, 3),
                            "threshold_seconds": crit_t,
                            "window": "5m",
                            "service": svc,
                        },
                    )
                )
            elif p95_sec >= high_t:
                breaches.append(
                    RuleBreach(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        service_name=svc,
                        severity=IncidentSeverity.HIGH,
                        observed_value=round(p95_sec, 3),
                        threshold_value=round(high_t, 3),
                        title=f"High P95 latency on {svc} ({round(p95_sec, 2)}s)",
                        description=(
                            f"Service {svc} p95 latency reached {round(p95_sec, 2)}s "
                            f"(high threshold: {high_t}s)."
                        ),
                        incident_type="p95_latency",
                        fingerprint=fingerprint,
                        evidence={
                            "rule_id": self.rule_id,
                            "promql": query,
                            "observed_p95_seconds": round(p95_sec, 3),
                            "threshold_seconds": high_t,
                            "window": "5m",
                            "service": svc,
                        },
                    )
                )
            else:
                healthy_fingerprints.append(fingerprint)

        return breaches, healthy_fingerprints


class PaymentFailureSpikeRule(BaseDetectionRule):
    """Detects spikes in payment transaction declines and failures."""

    def __init__(self, enabled: bool = True) -> None:
        super().__init__(
            rule_id="payment_failure_spike",
            name="Payment Failure Spike",
            description="Ratio of failed payment transactions exceeds threshold over 5m window",
            enabled=enabled,
        )

    async def evaluate(
        self, client: PrometheusClient, settings: Settings
    ) -> tuple[list[RuleBreach], list[str]]:
        if not self.enabled:
            return [], []

        svc = "payment-service"
        fingerprint = self.generate_fingerprint(svc)

        query = (
            '100 * sum(increase(payments_processed_total{status="FAILED"}[5m])) '
            "/ clamp_min(sum(increase(payments_processed_total[5m])), 1)"
        )
        sample_query = "sum(increase(payments_processed_total[5m]))"

        ratio_result = await client.query_instant(query)
        sample_result = await client.query_instant(sample_query)

        if not ratio_result.success or not ratio_result.samples:
            return [], []

        total_samples = sample_result.samples[0].value if sample_result.samples else 0.0
        if total_samples < settings.MIN_PAYMENT_SAMPLES:
            return [], [fingerprint]

        failure_pct = ratio_result.samples[0].value
        crit_t = settings.PAYMENT_FAILURE_CRITICAL_THRESHOLD
        high_t = settings.PAYMENT_FAILURE_HIGH_THRESHOLD

        if failure_pct >= crit_t:
            breach = RuleBreach(
                rule_id=self.rule_id,
                rule_name=self.name,
                service_name=svc,
                severity=IncidentSeverity.CRITICAL,
                observed_value=round(failure_pct, 2),
                threshold_value=crit_t,
                title=f"Critical payment failure spike ({round(failure_pct, 1)}%)",
                description=(
                    f"Payment failure rate reached {round(failure_pct, 1)}% "
                    f"(critical threshold: {crit_t}%)."
                ),
                incident_type="payment_failure",
                fingerprint=fingerprint,
                evidence={
                    "rule_id": self.rule_id,
                    "promql": query,
                    "observed_failure_percentage": round(failure_pct, 2),
                    "threshold_percentage": crit_t,
                    "total_payment_attempts": round(total_samples),
                    "window": "5m",
                    "service": svc,
                },
            )
            return [breach], []

        if failure_pct >= high_t:
            breach = RuleBreach(
                rule_id=self.rule_id,
                rule_name=self.name,
                service_name=svc,
                severity=IncidentSeverity.HIGH,
                observed_value=round(failure_pct, 2),
                threshold_value=high_t,
                title=f"Elevated payment failure rate ({round(failure_pct, 1)}%)",
                description=(
                    f"Payment failure rate reached {round(failure_pct, 1)}% "
                    f"(high threshold: {high_t}%)."
                ),
                incident_type="payment_failure",
                fingerprint=fingerprint,
                evidence={
                    "rule_id": self.rule_id,
                    "promql": query,
                    "observed_failure_percentage": round(failure_pct, 2),
                    "threshold_percentage": high_t,
                    "total_payment_attempts": round(total_samples),
                    "window": "5m",
                    "service": svc,
                },
            )
            return [breach], []

        return [], [fingerprint]


class ServiceUnavailableRule(BaseDetectionRule):
    """Detects when an application service scrape target is unreachable (up == 0)."""

    def __init__(self, enabled: bool = True) -> None:
        super().__init__(
            rule_id="service_unavailable",
            name="Service Unavailable (Uptime Probe)",
            description="Service endpoint unreachable by Prometheus scrape targets",
            enabled=enabled,
        )

    async def evaluate(
        self, client: PrometheusClient, settings: Settings
    ) -> tuple[list[RuleBreach], list[str]]:
        if not self.enabled:
            return [], []

        query = 'up{job=~"api-gateway|order-service|payment-service"}'
        result = await client.query_instant(query)
        if not result.success:
            return [], []

        breaches: list[RuleBreach] = []
        healthy_fingerprints: list[str] = []

        for sample in result.samples:
            svc = sample.labels.get("service") or sample.labels.get("job")
            if not svc:
                continue

            is_up = sample.value == 1.0
            fingerprint = self.generate_fingerprint(svc)

            if not is_up:
                breaches.append(
                    RuleBreach(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        service_name=svc,
                        severity=IncidentSeverity.CRITICAL,
                        observed_value=0.0,
                        threshold_value=1.0,
                        title=f"Service outage: {svc} is unreachable",
                        description=f"Prometheus target probe reports {svc} is down (up == 0).",
                        incident_type="service_unavailable",
                        fingerprint=fingerprint,
                        evidence={
                            "rule_id": self.rule_id,
                            "promql": query,
                            "instance": sample.labels.get("instance"),
                            "status": "UNREACHABLE",
                            "observed_up_value": 0,
                            "service": svc,
                        },
                    )
                )
            else:
                healthy_fingerprints.append(fingerprint)

        return breaches, healthy_fingerprints


class DbPoolExhaustionRule(BaseDetectionRule):
    """Detects database connection pool saturation approaching capacity."""

    def __init__(self, enabled: bool = True) -> None:
        super().__init__(
            rule_id="db_pool_exhaustion",
            name="Database Connection Pool Exhaustion",
            description="Database active checked-out connections exceed safe pool capacity",
            enabled=enabled,
        )

    async def evaluate(
        self, client: PrometheusClient, settings: Settings
    ) -> tuple[list[RuleBreach], list[str]]:
        if not self.enabled:
            return [], []

        query = (
            "100 * (sum by (service) (db_pool_checked_out) / "
            "clamp_min(sum by (service) (db_pool_size), 1))"
        )
        result = await client.query_instant(query)
        if not result.success:
            return [], []

        breaches: list[RuleBreach] = []
        healthy_fingerprints: list[str] = []

        crit_t = settings.DB_POOL_CRITICAL_THRESHOLD
        high_t = settings.DB_POOL_HIGH_THRESHOLD

        for sample in result.samples:
            svc = sample.labels.get("service")
            if not svc:
                continue

            pct = sample.value
            fingerprint = self.generate_fingerprint(svc)

            if pct >= crit_t:
                breaches.append(
                    RuleBreach(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        service_name=svc,
                        severity=IncidentSeverity.CRITICAL,
                        observed_value=round(pct, 1),
                        threshold_value=crit_t,
                        title=f"Critical DB pool saturation on {svc} ({round(pct)}%)",
                        description=(
                            f"Database connection pool for {svc} reached {round(pct)}% checked-out "
                            f"(critical threshold: {crit_t}%)."
                        ),
                        incident_type="db_pool_saturation",
                        fingerprint=fingerprint,
                        evidence={
                            "rule_id": self.rule_id,
                            "promql": query,
                            "saturation_percentage": round(pct, 1),
                            "threshold_percentage": crit_t,
                            "service": svc,
                        },
                    )
                )
            elif pct >= high_t:
                breaches.append(
                    RuleBreach(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        service_name=svc,
                        severity=IncidentSeverity.HIGH,
                        observed_value=round(pct, 1),
                        threshold_value=high_t,
                        title=f"High DB pool saturation on {svc} ({round(pct)}%)",
                        description=(
                            f"Database connection pool for {svc} reached {round(pct)}% checked-out "
                            f"(high threshold: {high_t}%)."
                        ),
                        incident_type="db_pool_saturation",
                        fingerprint=fingerprint,
                        evidence={
                            "rule_id": self.rule_id,
                            "promql": query,
                            "saturation_percentage": round(pct, 1),
                            "threshold_percentage": high_t,
                            "service": svc,
                        },
                    )
                )
            else:
                healthy_fingerprints.append(fingerprint)

        return breaches, healthy_fingerprints


class HighOrderFailureRateRule(BaseDetectionRule):
    """Detects elevated order creation failures due to downstream rejection or payment errors."""

    def __init__(self, enabled: bool = True) -> None:
        super().__init__(
            rule_id="high_order_failure_rate",
            name="High Order Failure Rate",
            description="Ratio of failed orders relative to total orders exceeds threshold",
            enabled=enabled,
        )

    async def evaluate(
        self, client: PrometheusClient, settings: Settings
    ) -> tuple[list[RuleBreach], list[str]]:
        if not self.enabled:
            return [], []

        svc = "order-service"
        fingerprint = self.generate_fingerprint(svc)

        query = (
            '100 * sum(increase(orders_created_total{status="PAYMENT_FAILED"}[5m])) '
            "/ clamp_min(sum(increase(orders_created_total[5m])), 1)"
        )
        sample_query = "sum(increase(orders_created_total[5m]))"

        ratio_result = await client.query_instant(query)
        sample_result = await client.query_instant(sample_query)

        if not ratio_result.success or not ratio_result.samples:
            return [], []

        total_orders = sample_result.samples[0].value if sample_result.samples else 0.0
        if total_orders < settings.MIN_TRAFFIC_REQUESTS:
            return [], [fingerprint]

        failure_pct = ratio_result.samples[0].value
        crit_t = settings.ORDER_FAILURE_CRITICAL_THRESHOLD
        high_t = settings.ORDER_FAILURE_HIGH_THRESHOLD

        if failure_pct >= crit_t:
            breach = RuleBreach(
                rule_id=self.rule_id,
                rule_name=self.name,
                service_name=svc,
                severity=IncidentSeverity.CRITICAL,
                observed_value=round(failure_pct, 2),
                threshold_value=crit_t,
                title=f"Critical order failure rate ({round(failure_pct, 1)}%)",
                description=(
                    f"Order failure rate reached {round(failure_pct, 1)}% over 5m window "
                    f"(critical threshold: {crit_t}%)."
                ),
                incident_type="order_failure",
                fingerprint=fingerprint,
                evidence={
                    "rule_id": self.rule_id,
                    "promql": query,
                    "observed_failure_percentage": round(failure_pct, 2),
                    "threshold_percentage": crit_t,
                    "total_orders_in_window": round(total_orders),
                    "window": "5m",
                    "service": svc,
                },
            )
            return [breach], []

        if failure_pct >= high_t:
            breach = RuleBreach(
                rule_id=self.rule_id,
                rule_name=self.name,
                service_name=svc,
                severity=IncidentSeverity.HIGH,
                observed_value=round(failure_pct, 2),
                threshold_value=high_t,
                title=f"Elevated order failure rate ({round(failure_pct, 1)}%)",
                description=(
                    f"Order failure rate reached {round(failure_pct, 1)}% "
                    f"(high threshold: {high_t}%)."
                ),
                incident_type="order_failure",
                fingerprint=fingerprint,
                evidence={
                    "rule_id": self.rule_id,
                    "promql": query,
                    "observed_failure_percentage": round(failure_pct, 2),
                    "threshold_percentage": high_t,
                    "total_orders_in_window": round(total_orders),
                    "window": "5m",
                    "service": svc,
                },
            )
            return [breach], []

        return [], [fingerprint]


def get_default_rules() -> list[BaseDetectionRule]:
    """Return the complete suite of deterministic detection rules."""
    return [
        HighHttp5xxErrorRateRule(),
        HighP95LatencyRule(),
        PaymentFailureSpikeRule(),
        ServiceUnavailableRule(),
        DbPoolExhaustionRule(),
        HighOrderFailureRateRule(),
    ]
