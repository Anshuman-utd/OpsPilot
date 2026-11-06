import os

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_is_instrumented = False


def setup_telemetry(app: FastAPI, service_name: str) -> trace.Tracer:
    """Initialize OpenTelemetry tracer provider, OTLP exporter, and automatic instrumentation."""
    global _is_instrumented

    # Configure W3C tracecontext propagator globally
    set_global_textmap(TraceContextTextMapPropagator())

    # Build Resource identifying the microservice
    resource = Resource.create(attributes={SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    # Configure OTLP gRPC exporter
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    try:
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)
    except Exception:
        # Fallback to no-op processor in isolated test environments
        pass

    trace.set_tracer_provider(provider)

    # Instrument FastAPI routes
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls="metrics,health",
    )

    # Instrument HTTPX client calls once globally
    if not _is_instrumented:
        HTTPXClientInstrumentor().instrument()
        _is_instrumented = True

    return trace.get_tracer(service_name)
