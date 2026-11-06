import json
import logging
import os
import sys

from fastapi import FastAPI
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.logging_config import JsonFormatter
from common.telemetry import setup_telemetry


def test_json_formatter_without_span():
    """Verify JsonFormatter outputs structured JSON even when no trace is active."""
    formatter = JsonFormatter(service_name="test-service")
    record = logging.LogRecord(
        name="test-logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Test message without trace",
        args=(),
        exc_info=None,
    )

    formatted = formatter.format(record)
    data = json.loads(formatted)
    assert data["service"] == "test-service"
    assert data["message"] == "Test message without trace"
    assert "timestamp" in data
    assert "trace_id" not in data


def test_json_formatter_with_active_span():
    """Verify JsonFormatter extracts trace_id and span_id when an OpenTelemetry span is active."""
    tracer = setup_telemetry(FastAPI(), "test-service")

    with tracer.start_as_current_span("parent-operation") as span:
        formatter = JsonFormatter(service_name="test-service")
        record = logging.LogRecord(
            name="test-logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=25,
            msg="Test message inside active trace",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        data = json.loads(formatted)

        expected_trace_id = format(span.get_span_context().trace_id, "032x")
        expected_span_id = format(span.get_span_context().span_id, "016x")

        assert data["trace_id"] == expected_trace_id
        assert data["span_id"] == expected_span_id
        assert data["message"] == "Test message inside active trace"


def test_w3c_trace_context_propagation():
    """Verify W3C TraceContext propagator injects traceparent header into carrier dict."""
    tracer = setup_telemetry(FastAPI(), "test-service")
    propagator = TraceContextTextMapPropagator()

    with tracer.start_as_current_span("gateway-request") as span:
        headers = {}
        propagator.inject(headers)

        assert "traceparent" in headers
        expected_trace_id = format(span.get_span_context().trace_id, "032x")
        assert expected_trace_id in headers["traceparent"]
