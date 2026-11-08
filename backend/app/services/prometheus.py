import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger("opspilot.prometheus")


@dataclass
class PrometheusSample:
    """Normalized metric sample extracted from a Prometheus vector response."""

    labels: dict[str, str] = field(default_factory=dict)
    value: float = 0.0
    timestamp: float = 0.0


@dataclass
class PrometheusQueryResult:
    """Normalized query execution result."""

    success: bool
    query: str
    samples: list[PrometheusSample] = field(default_factory=list)
    error: str | None = None
    raw_response: dict[str, Any] | None = None


class PrometheusClient:
    """Asynchronous HTTPX-based Prometheus client with resilient error handling."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.PROMETHEUS_URL).rstrip("/")
        self.timeout = timeout_seconds or settings.PROMETHEUS_TIMEOUT_SECONDS

    async def check_health(self) -> bool:
        """Verify Prometheus connectivity and readiness."""
        url = f"{self.base_url}/-/healthy"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url)
                return resp.status_code == 200
        except Exception as exc:
            logger.warning("Prometheus health check failed at %s: %s", url, exc)
            return False

    async def query_instant(self, query: str) -> PrometheusQueryResult:
        """
        Execute an instant vector query against Prometheus /api/v1/query.
        Returns a normalized result handling errors, timeouts, and empty sets cleanly.
        """
        url = f"{self.base_url}/api/v1/query"
        params = {"query": query}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params)

                if resp.status_code != 200:
                    err_msg = f"Prometheus returned HTTP {resp.status_code}: {resp.text}"
                    logger.error("Prometheus query error: %s (query: %s)", err_msg, query)
                    return PrometheusQueryResult(success=False, query=query, error=err_msg)

                data = resp.json()
                if data.get("status") != "success":
                    err_msg = data.get("error", "Unknown Prometheus error")
                    logger.error("Prometheus query unsuccessful: %s (query: %s)", err_msg, query)
                    return PrometheusQueryResult(
                        success=False, query=query, error=err_msg, raw_response=data
                    )

                result_data = data.get("data", {})
                result_type = result_data.get("resultType")
                raw_results = result_data.get("result", [])

                samples: list[PrometheusSample] = []

                if result_type == "vector":
                    for item in raw_results:
                        labels = item.get("metric", {})
                        val_tuple = item.get("value", [0.0, "0.0"])
                        ts = float(val_tuple[0])
                        try:
                            val = float(val_tuple[1])
                        except (ValueError, TypeError):
                            val = 0.0

                        samples.append(PrometheusSample(labels=labels, value=val, timestamp=ts))
                elif result_type == "matrix":
                    # For range queries, take the latest point in each series
                    for item in raw_results:
                        labels = item.get("metric", {})
                        values = item.get("values", [])
                        if values:
                            latest = values[-1]
                            samples.append(
                                PrometheusSample(
                                    labels=labels,
                                    value=float(latest[1]),
                                    timestamp=float(latest[0]),
                                )
                            )

                return PrometheusQueryResult(
                    success=True,
                    query=query,
                    samples=samples,
                    raw_response=data,
                )

        except httpx.TimeoutException as exc:
            err_msg = f"Prometheus query timed out after {self.timeout}s: {exc}"
            logger.error(err_msg)
            return PrometheusQueryResult(success=False, query=query, error=err_msg)

        except httpx.RequestError as exc:
            err_msg = f"Failed to connect to Prometheus at {self.base_url}: {exc}"
            logger.error(err_msg)
            return PrometheusQueryResult(success=False, query=query, error=err_msg)

        except Exception as exc:
            err_msg = f"Unexpected error querying Prometheus: {exc}"
            logger.exception(err_msg)
            return PrometheusQueryResult(success=False, query=query, error=err_msg)
