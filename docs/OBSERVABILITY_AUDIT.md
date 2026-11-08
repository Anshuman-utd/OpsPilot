# OpsPilot — Comprehensive Observability Infrastructure Audit

**Date:** 2026-11-08  
**Auditor:** OpsPilot Senior SRE & System Architect  
**Status:** Complete & Verified  

---

## 1. Executive Summary

This audit evaluates the complete telemetry pipeline of OpsPilot across metrics (Prometheus), logs (Grafana Loki & Alloy), distributed traces (Grafana Tempo & OpenTelemetry Collector), and unified visualization (Grafana). All 12 Docker Compose containers were inspected, configurations validated, real-world request traffic generated, fault scenarios tested, and telemetry verified across the entire stack.

---

## 2. Component-by-Component Audit & Verification

### 2.1 Prometheus (Metrics Aggregation)

- **Component:** Prometheus v2.54.1 (`opspilot-prometheus`, Port: `9090`)
- **Configuration:** `infrastructure/prometheus/prometheus.yml`
  - Scrape interval: `5s`, Evaluation interval: `5s`
  - Scrape targets: `api-gateway:8001`, `order-service:8002`, `payment-service:8003`, `localhost:9090`
- **Expected Behavior:** All 3 application microservices and Prometheus self-metrics report `health: "up"`. Metrics exposed on `/metrics` endpoint are scraped without dropouts or scrape errors.
- **Verification Performed:**
  - Queried Prometheus targets API (`/api/v1/targets`): all 4 targets verified `health: "up"` with `lastError: ""`.
  - Queried `up` metric: returned value `1` for all instances.
  - Verified application metrics: `http_requests_total`, `http_request_duration_seconds_bucket`, `orders_created_total`, `payments_processed_total`, `payment_failures_total`, `db_pool_size`, `db_pool_checked_out`.
  - Validated PromQL queries for request rates, p95 latency, 5xx error rate, and payment failure ratios.
- **Issues Discovered:** None in core scraping; initial dashboard panel referenced order rate without duration factor.
- **Fixes Applied:** Added explicit Grafana dashboard panels for pool saturation and payment throughput.
- **Final Status:** **VERIFIED & OPERATIONAL**

---

### 2.2 Grafana Loki & Alloy (Log Collection & Aggregation)

- **Component:** Grafana Loki v3.1.1 (`opspilot-loki`, Port: `3100`) & Grafana Alloy v1.3.1 (`opspilot-alloy`, Port: `12345`)
- **Configuration:** `infrastructure/loki/loki-config.yaml` & `infrastructure/alloy/config.alloy`
- **Expected Behavior:**
  - Alloy discovers Docker containers matching `/opspilot-(.*)`.
  - Alloy collects container stdout/stderr JSON logs and forwards them to Loki.
  - Logs are queryable via LogQL in Loki and Grafana Explore.
  - Logs contain structured fields (`trace_id`, `span_id`, `service`, `level`, `order_id`).
- **Verification Performed:**
  - Generated test order via API Gateway (`POST /api/orders`).
  - Queried Loki streams via `/loki/api/v1/query_range?query={service="order-service"}`: verified log lines received with accurate timestamps, log levels, and contextual metadata.
- **Issues Discovered:**
  1. **High-Cardinality Label Anti-Pattern in Alloy:** `config.alloy` was indexing `trace_id` as a Loki stream label (`stage.labels { trace_id = ... }`). Because every transaction generates a distinct `trace_id`, indexing it as a label causes unbounded stream creation (label cardinality explosion).
  2. **Log Timestamp Replay Errors:** When replaying existing container logs after container recreation, Loki logged errors `entry too far behind` due to default TSDB sample age limits.
- **Fixes Applied:**
  1. Updated `infrastructure/alloy/config.alloy` to keep only low-cardinality labels (`service`, `level`) in `stage.labels`. Grafana's Loki data source uses a regex derivedField (`"trace_id":\s*"([0-9a-f]{32})"`) to extract trace IDs directly from the log text, ensuring trace navigation works without stream label explosion.
  2. Added `limits_config` in `infrastructure/loki/loki-config.yaml` with `reject_old_samples: false`, `creation_grace_period: 10m`, and `max_entries_limit_per_query: 5000`.
- **Final Status:** **VERIFIED & OPERATIONAL**

---

### 2.3 OpenTelemetry Collector & Grafana Tempo (Distributed Tracing)

- **Component:** OpenTelemetry Collector Contrib v0.109.0 (`opspilot-otel-collector`, Ports: `4317`, `4318`, `13133`) & Grafana Tempo v2.6.0 (`opspilot-tempo`, Port: `3200`)
- **Configuration:** `infrastructure/otel-collector/otel-collector-config.yaml` & `infrastructure/tempo/tempo-config.yaml`
- **Expected Behavior:**
  - Microservices export traces via OTLP gRPC to `otel-collector:4317`.
  - OpenTelemetry Collector processes batches and forwards to `tempo:4317`.
  - Tempo stores traces and provides HTTP query access on port `3200`.
  - W3C `traceparent` context is propagated across HTTP boundaries:
    `API Gateway -> Order Service -> Payment Service`.
- **Verification Performed:**
  - Verified Collector health check at `http://localhost:13133`: returned `HTTP 200 {"status":"Server available"}`.
  - Generated order request: `POST /api/orders`.
  - Extracted trace ID from logs: `ceb4935f2a22dfc44581590dba38aa5c`.
  - Queried Tempo HTTP API: `GET /api/traces/ceb4935f2a22dfc44581590dba38aa5c`.
  - Verified full distributed call tree in Tempo:
    1. `api-gateway`: `POST /api/orders` (FastAPI span)
    2. `api-gateway`: `POST /orders` (HTTPX client span with traceparent injection)
    3. `order-service`: `POST /orders` (FastAPI server span, child of Gateway HTTPX span)
    4. `order-service`: `POST http://payment-service:8003/payments` (HTTPX client span)
    5. `payment-service`: `POST /payments` (FastAPI server span, child of Order HTTPX span)
- **Issues Discovered:**
  - Collector batch processor sizing was previously misconfigured (`send_batch_max_size: 1000` with unspecified `send_batch_size`).
- **Fixes Applied:**
  - Confirmed and committed batch processor tuning: `send_batch_size: 512`, `send_batch_max_size: 1000`.
- **Final Status:** **VERIFIED & OPERATIONAL**

---

### 2.4 Grafana (Unified Visualization & Cross-Linking)

- **Component:** Grafana v11.2.0 (`opspilot-grafana`, Port: `3001`)
- **Configuration:**
  - Data sources: `infrastructure/grafana/provisioning/datasources/datasources.yaml`
  - Dashboard: `infrastructure/grafana/provisioning/dashboards/opspilot-services.json`
- **Expected Behavior:**
  - Prometheus, Tempo, and Loki pre-provisioned on startup.
  - Loki log view provides "View Trace in Tempo" button next to extracted trace IDs.
  - Tempo provides "tracesToLogsV2" linking back to Loki.
  - Dashboard panels render live metrics for traffic, latency, errors, orders, and payments.
- **Verification Performed:**
  - Queried Grafana Data Sources API (`/api/datasources`): all 3 data sources provisioned.
  - Verified dashboard JSON queries against active Prometheus metrics.
- **Issues Discovered:**
  1. Data sources in `datasources.yaml` lacked explicit UIDs (`uid: prometheus`, `uid: tempo`), leading to auto-generated UIDs that could misalign with panel references.
  2. Order panel title stated "per Minute", while query was per-second without rate scaling.
- **Fixes Applied:**
  1. Explicitly pinned `uid: prometheus`, `uid: tempo`, `uid: loki` in `datasources.yaml`.
  2. Fixed panel title to `Orders Created Rate (req/s)` and added `Payment Transactions Rate (req/s)`, `Database Connection Pool Saturation (%)`, and `Downstream RPC Call Rate` panels.
- **Final Status:** **VERIFIED & OPERATIONAL**

---

### 2.5 Microservice Instrumentation & Fault Injection

- **Component:** API Gateway (`:8001`), Order Service (`:8002`), Payment Service (`:8003`)
- **Expected Behavior:**
  - Fast, non-blocking telemetry middleware.
  - 6 fault scenarios managed via `/debug/faults` with TTL auto-expiration and instant reset.
- **Verification Performed:**
  - Enabled `payment_failure_spike` with `failure_rate: 1.0` and `duration_seconds: 60`.
  - Sent 3 order requests through API Gateway.
  - Confirmed orders failed with `status: "PAYMENT_FAILED"`.
  - Confirmed Prometheus recorded `payment_failures_total = 3` and `payments_processed_total{status="FAILED"} = 3`.
  - Confirmed failed transaction logs appeared in Loki.
  - Confirmed Tempo recorded the error spans.
  - Reset all faults via `POST /debug/faults/reset` and confirmed return to healthy status.
- **Issues Discovered:**
  1. `POST /debug/faults` directly with body payload returned `405 Method Not Allowed` because the router only supported `/debug/faults/{name}/enable`.
  2. API Gateway did not log structured JSON on successful order creation, omitting `trace_id` from Gateway success logs.
- **Fixes Applied:**
  1. Added generic `POST /debug/faults` handler supporting `{fault_type, enabled, parameters, duration_seconds}` payloads.
  2. Added structured JSON log with `trace_id` in API Gateway on order creation success.
- **Final Status:** **VERIFIED & OPERATIONAL**

---

## 3. Telemetry Verification Matrix

| Telemetry Signal | Source Component | Aggregator / Pipeline | Destination | Verification Status |
|:---|:---|:---|:---|:---:|
| **Metrics** | Microservices `/metrics` | Direct Scrape (5s) | Prometheus (`:9090`) | **PASS** |
| **Logs** | Microservices Stdout/Stderr | Docker Discovery -> Alloy (`:12345`) | Grafana Loki (`:3100`) | **PASS** |
| **Traces** | OpenTelemetry SDK (W3C) | OTLP gRPC (`:4317`) -> Collector | Grafana Tempo (`:3200`) | **PASS** |
| **Trace-to-Log** | Tempo Spans | Grafana `tracesToLogsV2` | Loki Query | **PASS** |
| **Log-to-Trace** | Loki JSON logs | Grafana `derivedFields` Regex | Tempo Waterfall | **PASS** |
| **Fault Controls** | `/debug/faults` | In-Memory `FaultManager` with TTL | Services | **PASS** |

The observability infrastructure is fully verified, operational, and ready to feed real telemetry into **Phase 3: Incident Management & Deterministic Incident Detection**.
