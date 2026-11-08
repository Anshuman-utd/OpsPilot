# OpsPilot

> **Agentic AI-Powered Site Reliability Engineering (SRE) Platform**
> Autonomously investigates application incidents, identifies potential root causes, recommends verified remediation, and validates service recovery.

---

## 1. Project Overview & Problem Statement

Traditional monitoring and observability stacks (Prometheus, Grafana, Datadog) notify engineering teams when high error rates or latency spikes occur. However, diagnosing *why* an incident occurred still requires manual, high-stress investigation:
- Triaging logs across disparate microservices under tight SLA windows.
- Correlating metric anomalies with recent deployments and config changes.
- Navigating distributed traces across complex distributed call graphs.
- Formulating, testing, and discarding hypotheses while services degrade.

**OpsPilot** transforms this workflow into an autonomous, collaborative SRE co-pilot:
- **What went wrong?** Ingests telemetry to pinpoint anomalies deterministically.
- **Which service is affected?** Maps service dependencies and blast radius.
- **Why did the incident happen?** Synthesizes logs, metrics, and traces into evidence-backed root cause hypotheses.
- **What remediation should be performed?** Proposes safe, scoped runbook actions for human review.
- **Did the remediation work?** Verifies service recovery and issues post-incident retrospectives.

---

## 2. Product Architecture & Phases

OpsPilot maintains a strict boundary between **observed facts** and **AI-generated hypotheses**. The platform never takes unverified actions against production environments and enforces Human-in-the-Loop (HITL) approval for all remediation.

### Current Implementation Status
- **Phase 1 (Foundation):** Completed & Verified.
- **Phase 2 (Microservices & Observability):** Completed & Audited.
- **Phase 3 (Incident Management & Deterministic Detection):** Completed & Verified.
- **Phase 4 (LangGraph AI Agents & Root Cause Analysis):** Up next.

```mermaid
flowchart TD
    subgraph Microservices ["Simulated Target Application (Phase 2)"]
        GW["API Gateway :8001"] --> OS["Order Service :8002"]
        OS --> PS["Payment Service :8003"]
        OS & PS --> DEMO_DB[("Demo PostgreSQL :5434")]
    end

    subgraph Observability ["Observability Pipeline (Audited in Phase 2/3)"]
        GW & OS & PS -.->|OTLP Traces| OTEL["OTel Collector :4317"]
        OTEL --> TEMPO["Grafana Tempo :3200"]
        PROM["Prometheus :9090"] -.->|Scrape 5s| GW & OS & PS
        GW & OS & PS -.->|Stdout Logs| ALLOY["Grafana Alloy :12345"]
        ALLOY --> LOKI["Grafana Loki :3100"]
        GRAFANA["Grafana :3001"] --> PROM & TEMPO & LOKI
    end

    subgraph CoreEngine ["OpsPilot Incident Engine (Phase 3)"]
        DETECTION["Deterministic Detection Engine"] -->|Query PromQL| PROM
        DETECTION -->|Deduplicate & Create| INCIDENT["Incident Management API :8000"]
        CELERY["Celery Scheduler / Beat"] -->|Periodic Pass (30s)| DETECTION
        PLATFORM_DB[("Platform PostgreSQL :5433")]
        PLATFORM_REDIS[("Platform Redis :6380")]
        INCIDENT --> PLATFORM_DB & PLATFORM_REDIS
    end

    subgraph UserInterface ["Modern SRE Dashboard :3000"]
        INCIDENT --> DASHBOARD["Next.js Incident Dashboard"]
        DASHBOARD -->|Acknowledge / Investigate / Resolve| INCIDENT
        DASHBOARD -->|Trigger Manual Evaluation| DETECTION
    end
```

---

## 3. Technology Stack

### Backend & Microservices
- **Language / Runtime:** Python 3.11+
- **API Framework:** FastAPI
- **Data Validation & Settings:** Pydantic v2 & Pydantic Settings
- **ORM & Database Client:** SQLAlchemy 2.0 (asyncio) + `asyncpg` / `aiosqlite`
- **Database Migrations:** Alembic (async)
- **Caching & Background Broker:** Redis (asyncio) & Celery Beat
- **HTTP Client:** HTTPX (asynchronous with W3C trace propagation)
- **Package & Environment Manager:** `uv`
- **Testing & Quality:** `pytest`, `pytest-asyncio`, `ruff`

### Observability & Telemetry
- **Distributed Tracing:** OpenTelemetry Python SDK & Grafana Tempo
- **Metrics Collection:** Prometheus Client (`/metrics`) & Prometheus Server
- **Structured Logging:** JSON stdout logger with trace correlation & Grafana Loki
- **Telemetry Collector:** OpenTelemetry Collector Contrib & Grafana Alloy
- **Visualization:** Grafana (pre-provisioned dashboards on host port `3001`)

### Frontend
- **Framework:** Next.js (App Router, React 19)
- **Language:** TypeScript
- **Styling:** Tailwind CSS

---

## 4. Deterministic Detection Rules (Phase 3)

The platform continuously evaluates 6 deterministic SRE rules against Prometheus:

1. **`high_http_5xx_rate`**: Server 5xx error percentage exceeds threshold (`>5%` High, `>20%` Critical).
2. **`high_p95_latency`**: 95th percentile request latency exceeds SLA (`>1.0s` High, `>3.0s` Critical).
3. **`payment_failure_spike`**: Percentage of failed payment transactions exceeds threshold (`>20%` High, `>50%` Critical).
4. **`service_unavailable`**: Service scrape target unreachable (`up == 0`) or missing entirely from discovery.
5. **`db_pool_exhaustion`**: Active database checked-out connections exceed pool capacity (`>75%` High, `>90%` Critical).
6. **`high_order_failure_rate`**: Percentage of failed orders exceeds threshold (`>20%` High, `>50%` Critical).

### Automated Deduplication & Lifecycle
- Every breach calculates a stable fingerprint: $\text{SHA256}(\text{rule\_id} + ":" + \text{service})[0:32]$.
- Active incidents are updated in place with incremented occurrence counts and refreshed evidence rather than creating duplicate alerts.
- **Resolved Incident Recurrence (Option B):** When a resolved incident breaches again, a new active incident is created while preserving historical post-mortems, enforced via a database partial unique index.
- **Safe Auto-Recovery:** Incidents only resolve when minimum traffic gates are satisfied; protected by configurable flapping dampening (`CONSECUTIVE_HEALTHY_CYCLES_FOR_RECOVERY`).
- Full state machine: `OPEN` $\to$ `ACKNOWLEDGED` $\to$ `INVESTIGATING` $\to$ `RESOLVED`.

---

## 5. Port Allocation Reference

| Component | Container Name | Host Port | Internal Port | Description |
|:---|:---|:---:|:---:|:---|
| **FastAPI Backend (Phase 1 & 3)** | Local / Host | `8000` | `8000` | Incident Engine & Platform APIs |
| **Next.js Web UI (Phase 1 & 3)** | Local / Host | `3000` | `3000` | SRE Incident Management Dashboard |
| **Platform PostgreSQL (Phase 1)** | `opspilot-postgres` | `5433` | `5432` | Core Platform Database (`incidents`, `events`) |
| **Platform Redis (Phase 1)** | `opspilot-redis` | `6380` | `6379` | Celery Task Broker & Caching |
| **Demo PostgreSQL (Phase 2)** | `opspilot-demo-postgres` | `5434` | `5432` | Demo Application Database (`orders`, `payments`) |
| **API Gateway (Phase 2)** | `opspilot-api-gateway` | `8001` | `8001` | Microservices Ingress Gateway |
| **Order Service (Phase 2)** | `opspilot-order-service` | `8002` | `8002` | Order Processing Microservice |
| **Payment Service (Phase 2)** | `opspilot-payment-service` | `8003` | `8003` | Payment Processing Microservice |
| **Prometheus (Phase 2)** | `opspilot-prometheus` | `9090` | `9090` | Time-Series Metrics Scraper & TSDB |
| **Grafana (Phase 2)** | `opspilot-grafana` | `3001` | `3000` | Observability Dashboards |
| **Grafana Loki (Phase 2)** | `opspilot-loki` | `3100` | `3100` | Centralized Log Aggregation Engine |
| **Grafana Tempo (Phase 2)** | `opspilot-tempo` | `3200` | `3200` | Distributed Trace Storage & Query |
| **OTel Collector (Phase 2)** | `opspilot-otel-collector` | `4317` / `4318` | `4317` / `4318` | OTLP gRPC / HTTP Ingestion |
| **Grafana Alloy (Phase 2)** | `opspilot-alloy` | `12345` | `12345` | Docker Log Forwarder |

---

## 6. Quick Start & Operations

### Step 1: Start All Infrastructure (Docker Compose)
```bash
docker-compose up -d
```

### Step 2: Run Database Migrations
```bash
cd backend
uv run alembic upgrade head
```

### Step 3: Start Backend API
```bash
cd backend
uv run uvicorn app.main:app --reload --port 8000
```
Interactive API documentation is accessible at [http://localhost:8000/docs](http://localhost:8000/docs).

### Step 4: Start Frontend Web UI
```bash
cd frontend
npm run dev
```
Open [http://localhost:3000](http://localhost:3000) for the Incidents Dashboard.

### Step 5: Trigger Manual Incident Detection Pass
```bash
curl -X POST http://localhost:8000/api/v1/incidents/evaluate | jq .
```

---

## 7. Automated Testing & Code Quality

OpsPilot features 66 passing unit and integration tests across the platform:

```bash
# Run Core Platform & Incident Engine tests (39 tests)
cd backend
uv run pytest -v

# Run Demo Microservices & Telemetry tests (27 tests)
cd ../sample-services
uv run pytest -v

# Code Formatting & Linting
cd ../backend && uv run ruff check .
cd ../frontend && npm run lint && npm run build
```

---

## 8. Documentation Index

- **Observability Audit Report:** [`docs/OBSERVABILITY_AUDIT.md`](docs/OBSERVABILITY_AUDIT.md)
- **Phase 3 Implementation Guide:** [`docs/PHASE_3_IMPLEMENTATION.md`](docs/PHASE_3_IMPLEMENTATION.md)

---

## 9. License

OpsPilot is developed as an open-source portfolio project under the MIT License.
