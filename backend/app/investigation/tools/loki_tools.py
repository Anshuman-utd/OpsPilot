"""Loki investigation tools for autonomous log retrieval and correlation."""

import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger("opspilot.investigation.tools.loki")

TRACE_ID_PATTERN = re.compile(
    r'(?:trace_id[=:]\s*["\']?|traceparent[=:]\s*["\']?00-)?([0-9a-fA-F]{16,32})'
)


@dataclass
class LogEntry:
    timestamp: float
    raw_text: str
    level: str
    service: str
    trace_id: str | None = None
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class LogSearchResult:
    tool_name: str
    query: str
    service: str
    success: bool
    logs: list[LogEntry] = field(default_factory=list)
    total_found: int = 0
    extracted_trace_ids: list[str] = field(default_factory=list)
    summary_text: str = ""
    error: str | None = None
    deep_link: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "query": self.query,
            "service": self.service,
            "success": self.success,
            "total_found": self.total_found,
            "extracted_trace_ids": self.extracted_trace_ids,
            "summary_text": self.summary_text,
            "error": self.error,
            "deep_link": self.deep_link,
            "logs": [
                {
                    "timestamp": log_item.timestamp,
                    "service": log_item.service,
                    "level": log_item.level,
                    "trace_id": log_item.trace_id,
                    "raw_text": log_item.raw_text[:500],
                }
                for log_item in self.logs
            ],
        }


def build_grafana_loki_link(logql: str) -> str:
    settings = get_settings()
    base = settings.GRAFANA_URL.rstrip("/")
    escaped_query = urllib.parse.quote(logql)
    return f"{base}/explore?left=%7B%22datasource%22:%22Loki%22,%22queries%22:%5B%7B%22expr%22:%22{escaped_query}%22%7D%5D%7D"


class LokiInvestigationTool:
    """Specialized Loki query client with correlation and bounded retrieval."""

    def __init__(self, base_url: str | None = None, timeout_seconds: float = 8.0) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.LOKI_URL).rstrip("/")
        self.timeout = timeout_seconds

    async def query_range(
        self,
        query: str,
        start: float,
        end: float,
        limit: int = 50,
        direction: str = "BACKWARD",
    ) -> dict[str, Any]:
        """Execute a range query against Loki /loki/api/v1/query_range."""
        url = f"{self.base_url}/loki/api/v1/query_range"
        # Convert float seconds to nanoseconds string for Loki
        start_ns = str(int(start * 1e9))
        end_ns = str(int(end * 1e9))
        params = {
            "query": query,
            "start": start_ns,
            "end": end_ns,
            "limit": str(min(limit, 100)),
            "direction": direction,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                raise RuntimeError(f"Loki returned HTTP {resp.status_code}: {resp.text}")
            return resp.json()

    def _parse_loki_response(
        self,
        tool_name: str,
        service: str,
        query: str,
        raw_data: dict[str, Any],
    ) -> LogSearchResult:
        status = raw_data.get("status")
        if status != "success":
            return LogSearchResult(
                tool_name=tool_name,
                query=query,
                service=service,
                success=False,
                error=raw_data.get("error", "Unknown Loki query failure"),
                deep_link=build_grafana_loki_link(query),
            )

        data = raw_data.get("data", {})
        result_type = data.get("resultType")
        streams = data.get("result", [])

        logs: list[LogEntry] = []
        found_trace_ids: set[str] = set()

        if result_type == "streams":
            for stream_item in streams:
                stream_labels = stream_item.get("stream", {})
                svc = stream_labels.get("service") or stream_labels.get("app") or service
                stream_level = stream_labels.get("level", "INFO").upper()

                values = stream_item.get("values", [])
                for val_entry in values:
                    # val_entry is [nanoseconds_str, log_message]
                    try:
                        ts_sec = float(val_entry[0]) / 1e9
                    except (ValueError, TypeError, IndexError):
                        ts_sec = 0.0

                    line = val_entry[1] if len(val_entry) > 1 else ""

                    # Extract level from line if not in stream labels
                    level = stream_level
                    if "ERROR" in line or "error" in line:
                        level = "ERROR"
                    elif "WARN" in line or "warning" in line:
                        level = "WARN"

                    # Extract trace ID
                    m = TRACE_ID_PATTERN.search(line)
                    trace_id = m.group(1) if m else None
                    if trace_id:
                        found_trace_ids.add(trace_id)

                    logs.append(
                        LogEntry(
                            timestamp=ts_sec,
                            raw_text=line[:500],  # Bounded to prevent prompt bloat
                            level=level,
                            service=svc,
                            trace_id=trace_id,
                            labels=stream_labels,
                        )
                    )

        # Sort logs by timestamp ascending
        logs.sort(key=lambda x: x.timestamp)

        summary = (
            f"Retrieved {len(logs)} log entries for '{service}'. "
            f"Found {len(found_trace_ids)} distinct trace ID(s)."
        )

        return LogSearchResult(
            tool_name=tool_name,
            query=query,
            service=service,
            success=True,
            logs=logs,
            total_found=len(logs),
            extracted_trace_ids=sorted(found_trace_ids),
            summary_text=summary,
            deep_link=build_grafana_loki_link(query),
        )

    async def search_error_logs(
        self, service: str, start: float, end: float, limit: int = 50
    ) -> LogSearchResult:
        """Search error, fatal, and exception log messages for a given service."""
        logql = f'{{service="{service}"}} |~ "(?i)(error|exception|fail|fatal|timed out|refused)"'
        try:
            raw = await self.query_range(logql, start, end, limit=limit)
            return self._parse_loki_response("search_error_logs", service, logql, raw)
        except Exception as exc:
            logger.error("Error querying Loki for error logs: %s", exc)
            return LogSearchResult(
                tool_name="search_error_logs",
                query=logql,
                service=service,
                success=False,
                error=str(exc),
                deep_link=build_grafana_loki_link(logql),
            )

    async def search_logs_by_keyword(
        self, service: str, keyword: str, start: float, end: float, limit: int = 50
    ) -> LogSearchResult:
        """Search logs matching a specific keyword or pattern."""
        # Sanitize keyword for LogQL regex / literal filter
        safe_kw = keyword.replace('"', '\\"')
        logql = f'{{service="{service}"}} |= "{safe_kw}"'
        try:
            raw = await self.query_range(logql, start, end, limit=limit)
            return self._parse_loki_response("search_logs_by_keyword", service, logql, raw)
        except Exception as exc:
            logger.error("Error querying Loki with keyword '%s': %s", keyword, exc)
            return LogSearchResult(
                tool_name="search_logs_by_keyword",
                query=logql,
                service=service,
                success=False,
                error=str(exc),
                deep_link=build_grafana_loki_link(logql),
            )

    async def get_logs_for_trace(
        self, trace_id: str, start: float | None = None, end: float | None = None, limit: int = 30
    ) -> LogSearchResult:
        """Find log lines matching a specific distributed trace ID across all services."""
        # Match any service stream containing the trace ID
        logql = f'{{service=~".+"}} |= "{trace_id}"'
        try:
            # If start and end are not provided, query standard 30m window around now
            import time

            now = time.time()
            s = start or (now - 1800)
            e = end or now
            raw = await self.query_range(logql, s, e, limit=limit)
            return self._parse_loki_response("get_logs_for_trace", "all", logql, raw)
        except Exception as exc:
            logger.error("Error querying Loki for trace_id %s: %s", trace_id, exc)
            return LogSearchResult(
                tool_name="get_logs_for_trace",
                query=logql,
                service="all",
                success=False,
                error=str(exc),
                deep_link=build_grafana_loki_link(logql),
            )
