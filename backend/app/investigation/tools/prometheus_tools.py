"""Prometheus investigation tools for autonomous telemetry analysis."""

import logging
import math
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings
from app.services.prometheus import PrometheusClient, PrometheusQueryResult

logger = logging.getLogger("opspilot.investigation.tools.prometheus")


@dataclass
class MetricDataPoint:
    timestamp: float
    value: float


@dataclass
class MetricSeriesData:
    labels: dict[str, str]
    points: list[MetricDataPoint]
    min_value: float = 0.0
    max_value: float = 0.0
    mean_value: float = 0.0
    latest_value: float = 0.0


@dataclass
class MetricInvestigationResult:
    tool_name: str
    query: str
    service: str
    success: bool
    series: list[MetricSeriesData] = field(default_factory=list)
    overall_trend: str = "stable"  # "increasing", "decreasing", "stable", "unknown"
    summary_text: str = ""
    error: str | None = None
    deep_link: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "query": self.query,
            "service": self.service,
            "success": self.success,
            "series_count": len(self.series),
            "overall_trend": self.overall_trend,
            "summary_text": self.summary_text,
            "error": self.error,
            "deep_link": self.deep_link,
            "series": [
                {
                    "labels": s.labels,
                    "min_value": round(s.min_value, 4),
                    "max_value": round(s.max_value, 4),
                    "mean_value": round(s.mean_value, 4),
                    "latest_value": round(s.latest_value, 4),
                    "point_count": len(s.points),
                }
                for s in self.series
            ],
        }


@dataclass
class WindowComparisonResult:
    tool_name: str
    query: str
    current_mean: float
    baseline_mean: float
    percentage_change: float
    is_anomaly: bool
    summary: str
    error: str | None = None


def compute_trend(points: list[MetricDataPoint]) -> str:
    """Calculate trend direction (increasing, decreasing, stable) across points."""
    if len(points) < 4:
        return "stable"

    mid = len(points) // 2
    first_half = [p.value for p in points[:mid] if not math.isnan(p.value)]
    second_half = [p.value for p in points[mid:] if not math.isnan(p.value)]

    if not first_half or not second_half:
        return "stable"

    mean_first = sum(first_half) / len(first_half)
    mean_second = sum(second_half) / len(second_half)

    if mean_first == 0:
        if mean_second > 0:
            return "increasing"
        return "stable"

    ratio = mean_second / mean_first
    if ratio >= 1.20:
        return "increasing"
    if ratio <= 0.80:
        return "decreasing"
    return "stable"


def build_prometheus_deep_link(
    query: str, start: float | None = None, end: float | None = None
) -> str:
    settings = get_settings()
    base = settings.PROMETHEUS_URL.rstrip("/")
    encoded = urllib.parse.quote(query)
    url = f"{base}/graph?g0.expr={encoded}&g0.tab=0"
    if start and end:
        url += f"&g0.range_input={max(int(end - start), 60)}s&g0.end_input={int(end)}"
    return url


class PrometheusInvestigationTool:
    """Specialized Prometheus tool executed by autonomous investigation agents."""

    def __init__(self, client: PrometheusClient | None = None) -> None:
        self.client = client or PrometheusClient()

    def _process_query_result(
        self,
        tool_name: str,
        service: str,
        query: str,
        result: PrometheusQueryResult,
        start: float,
        end: float,
    ) -> MetricInvestigationResult:
        if not result.success:
            return MetricInvestigationResult(
                tool_name=tool_name,
                query=query,
                service=service,
                success=False,
                error=result.error or "Unknown query execution failure",
                deep_link=build_prometheus_deep_link(query, start, end),
            )

        processed_series: list[MetricSeriesData] = []
        all_trends: list[str] = []

        for s in result.series:
            pts = [MetricDataPoint(timestamp=t, value=v) for t, v in s.values if not math.isnan(v)]
            if not pts:
                continue

            vals = [p.value for p in pts]
            min_v = min(vals)
            max_v = max(vals)
            mean_v = sum(vals) / len(vals)
            latest_v = pts[-1].value

            series_data = MetricSeriesData(
                labels=s.labels,
                points=pts,
                min_value=min_v,
                max_value=max_v,
                mean_value=mean_v,
                latest_value=latest_v,
            )
            processed_series.append(series_data)
            all_trends.append(compute_trend(pts))

        # Determine overall trend
        overall_trend = "stable"
        if "increasing" in all_trends:
            overall_trend = "increasing"
        elif "decreasing" in all_trends:
            overall_trend = "decreasing"

        # Generate human-readable summary
        if not processed_series:
            summary = (
                f"No time-series data points returned for service '{service}' in requested window."
            )
        else:
            latest_max = max(s.latest_value for s in processed_series)
            mean_all = sum(s.mean_value for s in processed_series) / len(processed_series)
            summary = (
                f"{tool_name} for '{service}': latest peak={latest_max:.3f}, "
                f"window mean={mean_all:.3f}, trend={overall_trend} across {len(processed_series)} series."
            )

        return MetricInvestigationResult(
            tool_name=tool_name,
            query=query,
            service=service,
            success=True,
            series=processed_series,
            overall_trend=overall_trend,
            summary_text=summary,
            deep_link=build_prometheus_deep_link(query, start, end),
        )

    async def get_service_error_rate(
        self, service: str, start: float, end: float, step: str = "15s"
    ) -> MetricInvestigationResult:
        """Query HTTP 5xx error rate ratio for the service over a time range."""
        query = (
            f'sum(rate(http_requests_total{{service="{service}", status_code=~"5.."}}[2m])) '
            f'/ clamp_min(sum(rate(http_requests_total{{service="{service}"}}[2m])), 0.001)'
        )
        result = await self.client.query_range(query=query, start=start, end=end, step=step)
        return self._process_query_result(
            "get_service_error_rate", service, query, result, start, end
        )

    async def get_service_p95_latency(
        self, service: str, start: float, end: float, step: str = "15s"
    ) -> MetricInvestigationResult:
        """Query 95th percentile response latency in seconds over a time range."""
        query = f'histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{{service="{service}"}}[2m])))'
        result = await self.client.query_range(query=query, start=start, end=end, step=step)
        return self._process_query_result(
            "get_service_p95_latency", service, query, result, start, end
        )

    async def get_service_request_rate(
        self, service: str, start: float, end: float, step: str = "15s"
    ) -> MetricInvestigationResult:
        """Query incoming HTTP request throughput (req/s) over a time range."""
        query = f'sum(rate(http_requests_total{{service="{service}"}}[2m]))'
        result = await self.client.query_range(query=query, start=start, end=end, step=step)
        return self._process_query_result(
            "get_service_request_rate", service, query, result, start, end
        )

    async def get_payment_failure_ratio(
        self, start: float, end: float, step: str = "15s"
    ) -> MetricInvestigationResult:
        """Query payment transaction failure percentage over a time range."""
        query = (
            '100 * sum(rate(payments_processed_total{status="FAILED"}[2m])) '
            "/ clamp_min(sum(rate(payments_processed_total[2m])), 0.001)"
        )
        result = await self.client.query_range(query=query, start=start, end=end, step=step)
        return self._process_query_result(
            "get_payment_failure_ratio", "payment-service", query, result, start, end
        )

    async def get_db_pool_saturation(
        self, service: str, start: float, end: float, step: str = "15s"
    ) -> MetricInvestigationResult:
        """Query DB connection pool checkout saturation percentage over a time range."""
        query = (
            f'100 * (sum(db_pool_checked_out{{service="{service}"}}) '
            f'/ clamp_min(sum(db_pool_size{{service="{service}"}}), 1))'
        )
        result = await self.client.query_range(query=query, start=start, end=end, step=step)
        return self._process_query_result(
            "get_db_pool_saturation", service, query, result, start, end
        )

    async def get_downstream_latency(
        self, service: str, target_service: str | None, start: float, end: float, step: str = "15s"
    ) -> MetricInvestigationResult:
        """Query client latency when communicating with downstream targets."""
        target_filter = f',target="{target_service}"' if target_service else ""
        query = f'histogram_quantile(0.95, sum by (le) (rate(downstream_request_duration_seconds_bucket{{service="{service}"{target_filter}}}[2m])))'
        result = await self.client.query_range(query=query, start=start, end=end, step=step)
        svc_name = f"{service}->{target_service}" if target_service else service
        return self._process_query_result(
            "get_downstream_latency", svc_name, query, result, start, end
        )

    async def compare_windows(
        self,
        query: str,
        current_window: tuple[float, float],
        baseline_window: tuple[float, float],
        step: str = "15s",
    ) -> WindowComparisonResult:
        """Compare metric behavior in current incident window against a healthy baseline window."""
        c_res = await self.client.query_range(
            query, current_window[0], current_window[1], step=step
        )
        b_res = await self.client.query_range(
            query, baseline_window[0], baseline_window[1], step=step
        )

        if not c_res.success:
            return WindowComparisonResult(
                tool_name="compare_windows",
                query=query,
                current_mean=0.0,
                baseline_mean=0.0,
                percentage_change=0.0,
                is_anomaly=False,
                summary=f"Failed to query current window: {c_res.error}",
                error=c_res.error,
            )

        if not b_res.success:
            return WindowComparisonResult(
                tool_name="compare_windows",
                query=query,
                current_mean=0.0,
                baseline_mean=0.0,
                percentage_change=0.0,
                is_anomaly=False,
                summary=f"Failed to query baseline window: {b_res.error}",
                error=b_res.error,
            )

        c_vals = [pt[1] for s in c_res.series for pt in s.values if not math.isnan(pt[1])]
        b_vals = [pt[1] for s in b_res.series for pt in s.values if not math.isnan(pt[1])]

        c_mean = sum(c_vals) / len(c_vals) if c_vals else 0.0
        b_mean = sum(b_vals) / len(b_vals) if b_vals else 0.0

        if b_mean > 0:
            pct_change = ((c_mean - b_mean) / b_mean) * 100.0
        elif c_mean > 0:
            pct_change = 100.0
        else:
            pct_change = 0.0

        is_anomaly = abs(pct_change) >= 50.0

        summary = (
            f"Baseline mean: {b_mean:.3f}, Current mean: {c_mean:.3f} "
            f"({pct_change:+.1f}% change). {'Anomaly detected' if is_anomaly else 'Within normal variation'}."
        )

        return WindowComparisonResult(
            tool_name="compare_windows",
            query=query,
            current_mean=c_mean,
            baseline_mean=b_mean,
            percentage_change=pct_change,
            is_anomaly=is_anomaly,
            summary=summary,
        )
