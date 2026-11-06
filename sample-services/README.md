# OpsPilot Demo Microservices

Simulated distributed e-commerce architecture for observability and incident investigation.

## Services
1. **API Gateway (:8001)**: Client ingress, routing, request aggregation, and downstream health.
2. **Order Service (:8002)**: Order creation, lifecycle management, and payment delegation.
3. **Payment Service (:8003)**: Simulated payment provider and transaction recording.

## Observability
- **OpenTelemetry SDK**: Distributed W3C trace propagation.
- **Prometheus Metrics**: HTTP rates, latencies, and business metrics exposed at `/metrics`.
- **Structured JSON Logging**: Logs with trace/span correlation for Loki.
- **Fault Injection**: Controlled, reversible fault simulations at `/debug/faults`.
