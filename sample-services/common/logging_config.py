import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace


class JsonFormatter(logging.Formatter):
    """Custom logging formatter that outputs structured JSON with trace correlation."""

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        log_payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Inject OpenTelemetry trace and span IDs if an active span exists
        current_span = trace.get_current_span()
        if current_span:
            span_context = current_span.get_span_context()
            if span_context and span_context.is_valid:
                log_payload["trace_id"] = format(span_context.trace_id, "032x")
                log_payload["span_id"] = format(span_context.span_id, "016x")

        # Include structured extra fields if provided
        if hasattr(record, "extra_fields") and isinstance(record.extra_fields, dict):
            for k, v in record.extra_fields.items():
                if k not in log_payload:
                    log_payload[k] = v

        # Format exception information if present
        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_payload)


def configure_logging(service_name: str, log_level: str = "INFO") -> logging.Logger:
    """Configure the root logger to output structured JSON to stdout."""
    logger = logging.getLogger(service_name)
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name=service_name))
    logger.addHandler(handler)
    logger.propagate = False

    return logger
