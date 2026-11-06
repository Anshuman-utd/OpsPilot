# OpsPilot — Phase 2 Architecture & Observability Overview

This document provides a technical specification of the Phase 2 implementation of **OpsPilot**: the distributed demo application, OpenTelemetry instrumentation pipeline, observability stack, and controlled fault injection framework.

---

## 1. Objectives & Scope

Phase 2 establishes the realistic, observable multi-service application environment that OpsPilot's deterministic detection engine (Phase 3) and LangGraph AI investigation agents (Phase 4) will monitor and diagnose.

Key goals achieved:
- **Simulated E-Commerce Pipeline:** API Gateway, Order Service, and Payment Service communicating asynchronously with dedicated datastores.
- **Unified Telemetry Pipeline:** Distributed W3C tracing via OpenTelemetry SDK and Grafana Tempo, Prometheus time-series metrics, and trace-correlated structured JSON logging in Grafana Loki.
- **Isolated Datastores:** Dedicated Demo PostgreSQL container (`demo-postgres` on host port `5434`) separate from the OpsPilot platform database (`5433`).
- **Controlled Fault Injection:** Six repeatable, bounded, and reversible fault scenarios exposed via local management endpoints.

---

## 2. Distributed Architecture

```mermaid
flowchart TD
    subgraph Clients ["Clients & UI"]
        UI["OpsPilot Frontend (:3000)"]
        CURL["CLI / Synthetic Traffic"]
    end

    subgraph Microservices ["Demo Microservices (:8001 - :8003)"]
        GW["API Gateway (:8001)"]
        ORD["Order Service (:8002)"]
        PAY["Payment Service (:8003)"]
        DEMO_DB[("Demo PostgreSQL (:5434)")]
    end

    subgraph ObservabilityStack ["Observability Stack"]
        COLLECTOR["OpenTelemetry Collector (:4317/:4318)"]
        TEMPO["Grafana Tempo (:3200)"]
        PROM["Prometheus (:9090)"]
        ALLOY["Grafana Alloy (:12345)"]
        LOKI["Grafana Loki (:3100)"]
        GRAFANA["Grafana Dashboards (:3001)"]
    end

    UI & CURL -->|HTTP| GW
    GW -->|HTTP (Trace Context)| ORD
    ORD -->|HTTP (Trace Context)| PAY
    ORD & PAY -->|AsyncPG Pool| DEMO_DB

    GW & ORD & PAY -.->|OTLP Traces| COLLECTOR
    COLLECTOR -->|OTLP| TEMPO
    PROM -.->|Scrape /metrics| GW & ORD & PAY
    ALLOY -.->|Container Stdout JSON| LOKI
    
    GRAFANA -->|Query| PROM
    GRAFANA -->|Query| LOKI
    GRAFANA -->|Query| TEMPO
    LOKI -.->|TraceID Derived Field| TEMPO
```

---

## 3. Microservice Specifications

### A. API Gateway (`:8001`)
- **Ingress & Routing:** Public entrypoint for order processing requests. Routes `POST /api/orders` to Order Service.
- **Health Aggregation:** `GET /services/health` concurrently checks health across Order and Payment microservices.
- **Resilience:** Enforces configurable client timeouts (default 5.0s) and translates downstream HTTP errors without swallowing context.

### B. Order Service (`:8002`)
- **Lifecycle Management:** Creates orders with initial state `PENDING`, delegates payment to Payment Service, and transitions final state to `CONFIRMED` or `PAYMENT_FAILED`.
- **Database Persistence:** Stores orders in the `orders` table using SQLAlchemy 2.0 with connection pooling.
- **Telemetry Propagation:** Injects W3C `traceparent` headers into outgoing Payment Service calls.

### C. Payment Service (`:8003`)
- **Transaction Processing:** Simulates payment settlement, card verification, and payment failure modes.
- **Persistence:** Records each transaction in the `payments` table.
- **Fault Target:** Serves as the primary simulated third-party dependency for latency spikes and network failures.

---

## 4. End-to-End Trace Context Propagation

Every request traversing OpsPilot is linked through W3C Trace Context:

```text
Client Request
      │
      ▼
[API Gateway]  Span: "POST /api/orders" (trace_id: 4fa44cd6ed07..., span_id: 686f0b4d...)
      │        Injects header: traceparent: 00-4fa44cd6ed07...-686f0b4d...-01
      ▼
[Order Service] Span: "POST /orders" (parent: 686f0b4d...)
      │         Span: "db.insert orders"
      │         Injects header: traceparent: 00-4fa44cd6ed07...-89ab12cd...-01
      ▼
[Payment Service] Span: "POST /payments" (parent: 89ab12cd...)
      │           Span: "db.insert payments"
      ▼
Response returns up the chain with unified TraceID across all spans.
```

Structured JSON logs automatically capture `trace_id` and `span_id`:
```json
{
  "timestamp": "2026-11-07T01:15:30.124Z",
  "level": "INFO",
  "service": "order-service",
  "trace_id": "4fa44cd6ed0754a0b795c4692f9dc46a",
  "span_id": "89ab12cd34ef5678",
  "message": "Order ord-123 finalized with status CONFIRMED",
  "order_id": "ord-123",
  "status": "CONFIRMED"
}
```

---

## 5. Port Allocations & Service Catalog

To prevent host port conflicts with existing projects (such as local PostgreSQL 5432 or Redis 6379), Phase 2 utilizes dedicated non-conflicting host ports:

| Service | Host Port | Container Port | Purpose |
| :--- | :---: | :---: | :--- |
| **OpsPilot Backend (Phase 1)** | `8000` | `8000` | Core Platform Management API |
| **OpsPilot Frontend (Phase 1)** | `3000` | `3000` | Platform Web UI |
| **OpsPilot PostgreSQL (Phase 1)** | `5433` | `5432` | Core Platform Database |
| **OpsPilot Redis (Phase 1)** | `6380` | `6379` | Platform Redis Cache / Celery Broker |
| **Demo PostgreSQL (Phase 2)** | `5434` | `5432` | Demo Application Database |
| **API Gateway (Phase 2)** | `8001` | `8001` | Microservices Ingress Gateway |
| **Order Service (Phase 2)** | `8002` | `8002` | Order Processing Microservice |
| **Payment Service (Phase 2)** | `8003` | `8003` | Payment Processing Microservice |
| **Prometheus (Phase 2)** | `9090` | `9090` | Time-Series Metrics Scraper & TSDB |
| **Grafana (Phase 2)** | `3001` | `3000` | Observability Dashboards (:3001 avoids :3000 UI) |
| **Grafana Loki (Phase 2)** | `3100` | `3100` | Centralized Log Aggregation Engine |
| **Grafana Tempo (Phase 2)** | `3200` | `3200` | Distributed Trace Storage & Query |
| **OTel Collector (Phase 2)** | `4317` / `4318` | `4317` / `4318` | OpenTelemetry gRPC / HTTP Ingestion |
| **Grafana Alloy (Phase 2)** | `12345` | `12345` | Log Collector Daemon & UI |

---

## 6. Prometheus Metrics Catalog

| Metric Name | Type | Labels | Description |
| :--- | :---: | :--- | :--- |
| `http_requests_total` | Counter | `service`, `method`, `endpoint`, `status_code` | Total HTTP requests handled |
| `http_request_duration_seconds` | Histogram | `service`, `method`, `endpoint`, `status_code` | Request duration distribution |
| `orders_created_total` | Counter | `status` | Total orders created by outcome |
| `payments_processed_total` | Counter | `status` | Total payments processed by outcome |
| `payment_failures_total` | Counter | `reason` | Total payment failures categorized by cause |
| `downstream_requests_total` | Counter | `service`, `target`, `status_code` | Requests made to downstream services |
| `downstream_request_duration_seconds` | Histogram | `service`, `target` | Downstream request latency distribution |
| `db_pool_size` | Gauge | `service` | Configured database connection pool size |
| `db_pool_checked_out` | Gauge | `service` | Active connections currently in use |

---

## 7. Controlled Fault Injection System

All faults are disabled by default (`FAULT_INJECTION_ENABLED=false`). When enabled, controls are accessible on ports 8001, 8002, and 8003.

### Fault Scenarios

| Scenario | Service Target | Default Parameters | Description & Expected SRE Evidence |
| :--- | :---: | :--- | :--- |
| `payment_failure_spike` | Payment Service (:8003) | `{"failure_rate": 0.8, "reason": "BANK_DECLINED"}` | 80% of payments fail. Error rate spikes in Payment Service; orders transition to `PAYMENT_FAILED`. |
| `downstream_latency_spike` | Payment Service (:8003) | `{"delay_seconds": 2.5}` | Bounded artificial delay injected into payment operations. p95 latency spikes on Gateway and Order Service. |
| `payment_service_unavailability`| Payment Service (:8003) | `{"status_code": 503, "error_message": "..."}` | Simulates payment dependency outage without killing containers. Gateway returns 502/503. |
| `db_pool_exhaustion` | Order Service (:8002) | `{"hold_seconds": 5.0, "connections_to_hold": 7}` | Satures connection pool. Subsequent requests block and time out waiting for connections. |
| `cascading_timeout` | Payment Service (:8003) | `{"delay_seconds": 5.0}` | Payment delay exceeds Order Service 3.0s timeout. Causes cascading 504 Gateway Timeout errors. |
| `order_service_error_spike` | Order Service (:8002) | `{"error_rate": 0.7, "error_message": "..."}` | Simulates internal order processing exceptions returning HTTP 500. |

### Controlling Faults via CLI

```bash
# Enable fault injection in environment
export FAULT_INJECTION_ENABLED=true

# 1. Trigger Payment Failure Spike (expires in 120 seconds)
curl -X POST http://localhost:8003/debug/faults/payment_failure_spike/enable \
  -H "Content-Type: application/json" \
  -d '{"duration_seconds": 120, "params": {"failure_rate": 0.9, "reason": "FRAUD_SUSPECTED"}}'

# 2. Trigger Downstream Latency Spike
curl -X POST http://localhost:8003/debug/faults/downstream_latency_spike/enable \
  -H "Content-Type: application/json" \
  -d '{"duration_seconds": 60, "params": {"delay_seconds": 2.5}}'

# 3. Check Active Faults
curl http://localhost:8003/debug/faults

# 4. Reset All Faults Immediately
curl -X POST http://localhost:8003/debug/faults/reset
```

---

## 8. Verification Workflow

Run the automated verification script to validate live services, telemetry pipelines, and datastores:

```bash
cd sample-services
uv run python verify_pipeline.py
```
