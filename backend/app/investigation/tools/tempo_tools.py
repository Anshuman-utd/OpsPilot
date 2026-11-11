"""Tempo investigation tools for autonomous distributed trace analysis and bottleneck detection."""

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger("opspilot.investigation.tools.tempo")


@dataclass
class SpanSummary:
    span_id: str
    parent_span_id: str | None
    service_name: str
    operation_name: str
    duration_ms: float
    start_time_unix_nano: int
    is_error: bool
    status_message: str | None = None
    http_status_code: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceSummary:
    trace_id: str
    root_service: str
    root_operation: str
    duration_ms: float
    has_error: bool
    start_time: float


@dataclass
class TraceSearchResult:
    tool_name: str
    service: str
    success: bool
    traces: list[TraceSummary] = field(default_factory=list)
    total_found: int = 0
    error: str | None = None
    deep_link: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "service": self.service,
            "success": self.success,
            "total_found": self.total_found,
            "traces": [
                {
                    "trace_id": t.trace_id,
                    "root_service": t.root_service,
                    "root_operation": t.root_operation,
                    "duration_ms": round(t.duration_ms, 2),
                    "has_error": t.has_error,
                    "start_time": t.start_time,
                }
                for t in self.traces
            ],
            "error": self.error,
            "deep_link": self.deep_link,
        }


@dataclass
class TraceDetailResult:
    tool_name: str
    trace_id: str
    success: bool
    root_span: SpanSummary | None = None
    bottleneck_span: SpanSummary | None = None
    failed_spans: list[SpanSummary] = field(default_factory=list)
    all_spans: list[SpanSummary] = field(default_factory=list)
    total_duration_ms: float = 0.0
    services_involved: list[str] = field(default_factory=list)
    summary_text: str = ""
    error: str | None = None
    deep_link: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "trace_id": self.trace_id,
            "success": self.success,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "services_involved": self.services_involved,
            "summary_text": self.summary_text,
            "root_span": (
                {
                    "service": self.root_span.service_name,
                    "operation": self.root_span.operation_name,
                    "duration_ms": round(self.root_span.duration_ms, 2),
                }
                if self.root_span
                else None
            ),
            "bottleneck_span": (
                {
                    "service": self.bottleneck_span.service_name,
                    "operation": self.bottleneck_span.operation_name,
                    "duration_ms": round(self.bottleneck_span.duration_ms, 2),
                }
                if self.bottleneck_span
                else None
            ),
            "failed_spans_count": len(self.failed_spans),
            "failed_spans": [
                {
                    "span_id": s.span_id,
                    "service": s.service_name,
                    "operation": s.operation_name,
                    "status_message": s.status_message,
                    "http_status": s.http_status_code,
                    "duration_ms": round(s.duration_ms, 2),
                }
                for s in self.failed_spans
            ],
            "error": self.error,
            "deep_link": self.deep_link,
        }


def build_grafana_tempo_link(trace_id: str) -> str:
    settings = get_settings()
    base = settings.GRAFANA_URL.rstrip("/")
    return f"{base}/explore?left=%7B%22datasource%22:%22Tempo%22,%22queries%22:%5B%7B%22query%22:%22{trace_id}%22%7D%5D%7D"


def _extract_attr_value(val_obj: dict[str, Any]) -> Any:
    if "stringValue" in val_obj:
        return val_obj["stringValue"]
    if "intValue" in val_obj:
        return int(val_obj["intValue"])
    if "doubleValue" in val_obj:
        return float(val_obj["doubleValue"])
    if "boolValue" in val_obj:
        return bool(val_obj["boolValue"])
    return str(val_obj)


class TempoInvestigationTool:
    """Specialized client for Tempo trace search, span correlation, and latency analysis."""

    def __init__(self, base_url: str | None = None, timeout_seconds: float = 8.0) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.TEMPO_URL).rstrip("/")
        self.timeout = timeout_seconds

    async def search_recent_traces(
        self,
        service: str,
        start: float,
        end: float,
        limit: int = 10,
        error_only: bool = False,
    ) -> TraceSearchResult:
        """Search distributed traces for a service within a given time range."""
        url = f"{self.base_url}/api/search"
        tags = f"service.name={service}"
        if error_only:
            tags += " status=error"

        params = {
            "tags": tags,
            "start": str(int(start)),
            "end": str(int(end)),
            "limit": str(min(limit, 30)),
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    return TraceSearchResult(
                        tool_name="search_recent_traces",
                        service=service,
                        success=False,
                        error=f"Tempo search returned HTTP {resp.status_code}: {resp.text}",
                    )

                data = resp.json()
                raw_traces = data.get("traces", [])
                traces: list[TraceSummary] = []

                for t in raw_traces:
                    tid = t.get("traceID") or t.get("traceId")
                    if not tid:
                        continue
                    dur = float(t.get("durationMs", 0.0))
                    root_svc = t.get("rootServiceName") or service
                    root_op = t.get("rootTraceName") or "unknown"
                    start_nano = int(t.get("startTimeUnixNano", 0))
                    start_sec = start_nano / 1e9 if start_nano else start

                    traces.append(
                        TraceSummary(
                            trace_id=tid,
                            root_service=root_svc,
                            root_operation=root_op,
                            duration_ms=dur,
                            has_error=error_only or bool(t.get("error", False)),
                            start_time=start_sec,
                        )
                    )

                return TraceSearchResult(
                    tool_name="search_recent_traces",
                    service=service,
                    success=True,
                    traces=traces,
                    total_found=len(traces),
                )

        except Exception as exc:
            logger.error("Error searching traces in Tempo for service %s: %s", service, exc)
            return TraceSearchResult(
                tool_name="search_recent_traces",
                service=service,
                success=False,
                error=str(exc),
            )

    async def get_trace_details(self, trace_id: str) -> TraceDetailResult:
        """Fetch trace details from Tempo and parse span hierarchy and errors."""
        url = f"{self.base_url}/api/traces/{trace_id}"
        deep_link = build_grafana_tempo_link(trace_id)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url)
                if resp.status_code == 404:
                    return TraceDetailResult(
                        tool_name="get_trace_details",
                        trace_id=trace_id,
                        success=False,
                        error=f"Trace ID '{trace_id}' not found in Tempo",
                        deep_link=deep_link,
                    )
                if resp.status_code != 200:
                    return TraceDetailResult(
                        tool_name="get_trace_details",
                        trace_id=trace_id,
                        success=False,
                        error=f"Tempo returned HTTP {resp.status_code}: {resp.text}",
                        deep_link=deep_link,
                    )

                data = resp.json()
                return self._parse_trace_json(trace_id, data, deep_link)

        except Exception as exc:
            logger.error("Error fetching trace %s from Tempo: %s", trace_id, exc)
            return TraceDetailResult(
                tool_name="get_trace_details",
                trace_id=trace_id,
                success=False,
                error=str(exc),
                deep_link=deep_link,
            )

    def _parse_trace_json(
        self, trace_id: str, raw_json: dict[str, Any], deep_link: str
    ) -> TraceDetailResult:
        batches = raw_json.get("batches", [])
        spans: list[SpanSummary] = []
        services_set: set[str] = set()

        for batch in batches:
            res = batch.get("resource", {})
            svc_name = "unknown"
            for attr in res.get("attributes", []):
                if attr.get("key") == "service.name":
                    svc_name = _extract_attr_value(attr.get("value", {}))
                    break
            services_set.add(svc_name)

            for scope_span in batch.get("scopeSpans", []):
                for s in scope_span.get("spans", []):
                    sid = s.get("spanId")
                    pid = s.get("parentSpanId") or None
                    op_name = s.get("name", "span")
                    start_nano = int(s.get("startTimeUnixNano", 0))
                    end_nano = int(s.get("endTimeUnixNano", 0))
                    dur_ms = (end_nano - start_nano) / 1e6 if end_nano > start_nano else 0.0

                    # Parse attributes
                    attrs: dict[str, Any] = {}
                    for a in s.get("attributes", []):
                        attrs[a.get("key")] = _extract_attr_value(a.get("value", {}))

                    # Parse status & error
                    status_obj = s.get("status", {})
                    # Code 2 is STATUS_CODE_ERROR in OTel
                    is_err = (
                        status_obj.get("code") == 2 or status_obj.get("code") == "STATUS_CODE_ERROR"
                    )
                    status_msg = status_obj.get("message")

                    http_status = attrs.get("http.status_code") or attrs.get(
                        "http.response.status_code"
                    )
                    if http_status and isinstance(http_status, int) and http_status >= 400:
                        is_err = True

                    spans.append(
                        SpanSummary(
                            span_id=sid,
                            parent_span_id=pid,
                            service_name=svc_name,
                            operation_name=op_name,
                            duration_ms=dur_ms,
                            start_time_unix_nano=start_nano,
                            is_error=is_err,
                            status_message=status_msg,
                            http_status_code=http_status,
                            attributes=attrs,
                        )
                    )

        if not spans:
            return TraceDetailResult(
                tool_name="get_trace_details",
                trace_id=trace_id,
                success=True,
                services_involved=list(services_set),
                summary_text=f"Trace {trace_id} contains 0 spans.",
                deep_link=deep_link,
            )

        # Root span has no parentSpanId
        root_span = next((s for s in spans if not s.parent_span_id), spans[0])
        total_duration = root_span.duration_ms if root_span else max(s.duration_ms for s in spans)

        # Bottleneck span: span with largest duration
        bottleneck = max(spans, key=lambda s: s.duration_ms)

        # Failed spans
        failed = [s for s in spans if s.is_error]

        summary = (
            f"Trace {trace_id}: total duration {total_duration:.1f}ms across {len(spans)} spans in "
            f"{len(services_set)} service(s) ({', '.join(sorted(services_set))}). "
            f"Bottleneck: {bottleneck.service_name}::{bottleneck.operation_name} ({bottleneck.duration_ms:.1f}ms). "
            f"Errors: {len(failed)} failed span(s)."
        )

        return TraceDetailResult(
            tool_name="get_trace_details",
            trace_id=trace_id,
            success=True,
            root_span=root_span,
            bottleneck_span=bottleneck,
            failed_spans=failed,
            all_spans=spans,
            total_duration_ms=total_duration,
            services_involved=sorted(services_set),
            summary_text=summary,
            deep_link=deep_link,
        )
