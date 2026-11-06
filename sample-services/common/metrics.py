import time
from collections.abc import Callable

from fastapi import Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware

# Standard HTTP metrics
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total count of HTTP requests processed by service",
    ["service", "method", "endpoint", "status_code"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["service", "method", "endpoint", "status_code"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# Business application metrics
ORDERS_CREATED_TOTAL = Counter(
    "orders_created_total",
    "Total orders created by status",
    ["status"],
)

PAYMENTS_PROCESSED_TOTAL = Counter(
    "payments_processed_total",
    "Total payments processed by status",
    ["status"],
)

PAYMENT_FAILURES_TOTAL = Counter(
    "payment_failures_total",
    "Total payment failures categorized by reason",
    ["reason"],
)

DOWNSTREAM_REQUESTS_TOTAL = Counter(
    "downstream_requests_total",
    "Total outgoing requests to downstream services",
    ["service", "target", "status_code"],
)

DOWNSTREAM_REQUEST_DURATION_SECONDS = Histogram(
    "downstream_request_duration_seconds",
    "Duration of downstream service HTTP calls in seconds",
    ["service", "target"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

DB_POOL_SIZE = Gauge(
    "db_pool_size",
    "Current configured size of the database connection pool",
    ["service"],
)

DB_POOL_CHECKED_OUT = Gauge(
    "db_pool_checked_out",
    "Number of active connections currently checked out from the pool",
    ["service"],
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Middleware that measures HTTP request latency and increments request counters."""

    def __init__(self, app: Callable, service_name: str) -> None:
        super().__init__(app)
        self.service_name = service_name

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Ignore prometheus scrape requests and health probes from bloating metrics
        if request.url.path in ("/metrics", "/health", "/favicon.ico"):
            return await call_next(request)

        start_time = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = time.perf_counter() - start_time
            # Normalize endpoint path to avoid cardinality explosion with IDs
            endpoint = request.url.path
            for prefix in ("/api/orders/", "/orders/", "/payments/"):
                if endpoint.startswith(prefix) and len(endpoint) > len(prefix):
                    endpoint = f"{prefix}{{id}}"
                    break

            HTTP_REQUESTS_TOTAL.labels(
                service=self.service_name,
                method=request.method,
                endpoint=endpoint,
                status_code=str(status_code),
            ).inc()

            HTTP_REQUEST_DURATION_SECONDS.labels(
                service=self.service_name,
                method=request.method,
                endpoint=endpoint,
                status_code=str(status_code),
            ).observe(duration)


def metrics_endpoint() -> Response:
    """Expose Prometheus formatted metrics."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
