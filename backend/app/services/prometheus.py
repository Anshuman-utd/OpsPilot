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
class PrometheusSeries:
    """Normalized time-series extracted from a Prometheus matrix response."""

    labels: dict[str, str] = field(default_factory=dict)
    values: list[tuple[float, float]] = field(default_factory=list)  # (timestamp, value)


@dataclass
class PrometheusQueryResult:
    """Normalized query execution result."""

    success: bool
    query: str
    samples: list[PrometheusSample] = field(default_factory=list)
    series: list[PrometheusSeries] = field(default_factory=list)
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
                series_list: list[PrometheusSeries] = []

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
                    for item in raw_results:
                        labels = item.get("metric", {})
                        raw_values = item.get("values", [])
                        parsed_values: list[tuple[float, float]] = []
                        for pt in raw_values:
                            try:
                                parsed_values.append((float(pt[0]), float(pt[1])))
                            except (ValueError, TypeError, IndexError):
                                continue
                        series_list.append(PrometheusSeries(labels=labels, values=parsed_values))
                        if parsed_values:
                            samples.append(
                                PrometheusSample(
                                    labels=labels,
                                    value=parsed_values[-1][1],
                                    timestamp=parsed_values[-1][0],
                                )
                            )

                return PrometheusQueryResult(
                    success=True,
                    query=query,
                    samples=samples,
                    series=series_list,
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

    async def query_range(
        self,
        query: str,
        start: float,
        end: float,
        step: str = "15s",
    ) -> PrometheusQueryResult:
        """
        Execute a range vector query against Prometheus /api/v1/query_range.
        Returns a normalized result containing series over the requested time window.
        """
        url = f"{self.base_url}/api/v1/query_range"
        params = {
            "query": query,
            "start": str(start),
            "end": str(end),
            "step": step,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params)

                if resp.status_code != 200:
                    err_msg = (
                        f"Prometheus query_range returned HTTP {resp.status_code}: {resp.text}"
                    )
                    logger.error("Prometheus query_range error: %s (query: %s)", err_msg, query)
                    return PrometheusQueryResult(success=False, query=query, error=err_msg)

                data = resp.json()
                if data.get("status") != "success":
                    err_msg = data.get("error", "Unknown Prometheus error")
                    logger.error(
                        "Prometheus query_range unsuccessful: %s (query: %s)", err_msg, query
                    )
                    return PrometheusQueryResult(
                        success=False, query=query, error=err_msg, raw_response=data
                    )

                result_data = data.get("data", {})
                raw_results = result_data.get("result", [])

                samples: list[PrometheusSample] = []
                series_list: list[PrometheusSeries] = []

                for item in raw_results:
                    labels = item.get("metric", {})
                    raw_values = item.get("values", [])
                    parsed_values: list[tuple[float, float]] = []
                    for pt in raw_values:
                        try:
                            parsed_values.append((float(pt[0]), float(pt[1])))
                        except (ValueError, TypeError, IndexError):
                            continue
                    series_list.append(PrometheusSeries(labels=labels, values=parsed_values))
                    if parsed_values:
                        samples.append(
                            PrometheusSample(
                                labels=labels,
                                value=parsed_values[-1][1],
                                timestamp=parsed_values[-1][0],
                            )
                        )

                return PrometheusQueryResult(
                    success=True,
                    query=query,
                    samples=samples,
                    series=series_list,
                    raw_response=data,
                )

        except httpx.TimeoutException as exc:
            err_msg = f"Prometheus query_range timed out after {self.timeout}s: {exc}"
            logger.error(err_msg)
            return PrometheusQueryResult(success=False, query=query, error=err_msg)

        except httpx.RequestError as exc:
            err_msg = f"Failed to connect to Prometheus at {self.base_url}: {exc}"
            logger.error(err_msg)
            return PrometheusQueryResult(success=False, query=query, error=err_msg)

        except Exception as exc:
            err_msg = f"Unexpected error executing query_range on Prometheus: {exc}"
            logger.exception(err_msg)
            return PrometheusQueryResult(success=False, query=query, error=err_msg)
