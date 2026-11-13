# OpsPilot

**Run the authenticated release locally or on a non-AWS VM** with the [deployment guide](docs/DEPLOYMENT.md). The [user guide](docs/USER_GUIDE.md) walks through signup, the demo lab, incident response, and report export. See the [architecture and security boundaries](docs/ARCHITECTURE.md) and [evaluation guide](docs/EVALUATION.md). The instructions below also cover host-based development.

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
- **Did the remediation work?** Records telemetry observations and reports the recovery outcome.

---

## 2. Product Architecture & Phases

OpsPilot maintains a strict boundary between **observed facts** and **AI-generated hypotheses**. The platform never takes unverified actions against production environments and enforces Human-in-the-Loop (HITL) approval for all remediation.

### Current implementation status

OpsPilot implements incident detection, LangGraph investigation, and operator-approved remediation against its three demo microservices. See the [repository audit and next steps](docs/PROJECT_AUDIT.md) for current validation evidence and deployment gaps.

```mermaid
flowchart TD
    subgraph Microservices ["Simulated Target Application (Phase 2)"]
        GW["API Gateway :8001"] --> OS["Order Service :8002"]
        OS --> PS["Payment Service :8003"]
        OS & PS --> DEMO_DB[("Demo PostgreSQL :5434")]
    end

    subgraph Observability ["Observability Pipeline (Phase 2)"]
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

    subgraph InvestigationSwarm ["Autonomous Investigation Swarm (Phase 4 LangGraph)"]
        CELERY -->|Async Task| WORKFLOW["LangGraph Multi-Agent Workflow"]
        WORKFLOW --> ANALYZER["Incident Analyzer"]
        ANALYZER --> METRICS_AG["Metrics Investigator (Prometheus)"]
        ANALYZER --> LOG_AG["Log Investigator (Loki)"]
        ANALYZER --> TRACE_AG["Trace Investigator (Tempo)"]
        ANALYZER --> HIST_AG["Historical Context (PostgreSQL)"]
        METRICS_AG & LOG_AG & TRACE_AG & HIST_AG --> AGGREGATOR["Evidence Aggregator"]
        AGGREGATOR --> RCA_SYNTH["RCA Synthesizer (Groq / Heuristic)"]
        RCA_SYNTH --> VALIDATOR["Evidence Validator & Calibrator"]
        VALIDATOR --> FINAL_REPORT["Verified Final RCA Report"]
    end

    subgraph RemediationEngine ["Controlled Remediation & Recovery Engine (Phase 5)"]
        FINAL_REPORT --> PLANNER["Remediation Planner (LLM + Fallback)"]
        PLANNER --> REGISTRY["Allowlisted Action Registry"]
        REGISTRY --> POLICY["Deterministic Safety Policy"]
        POLICY --> APPROVAL_GATE["Mandatory Human Approval Gate"]
        APPROVAL_GATE -->|Token-authenticated approval| EXEC_TASK["Celery Execution Worker"]
        EXEC_TASK -->|Redis Lock + Bounded HTTP| OS & PS & GW
        EXEC_TASK --> VERIF_TASK["Celery Verification Worker"]
        VERIF_TASK -->|Query Consecutive Cycles| PROM
        VERIF_TASK -->|3 Cycles Healthy| RESOLVED_INC["Auto-Resolve Incident & Audit Trail"]
    end

    subgraph UserInterface ["Modern SRE Dashboard :3000"]
        INCIDENT --> DASHBOARD["Next.js Incident Dashboard"]
        DASHBOARD -->|Start Investigation / View RCA| WORKFLOW
        DASHBOARD -->|Propose Plan & Approve Action| RemediationEngine
        DASHBOARD -->|Acknowledge / Resolve| INCIDENT
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

## 5. Autonomous Multi-Agent Investigation & RCA (Phase 4)

When an operator requests an investigation, OpsPilot dispatches the **LangGraph** workflow:

1. **Incident Analyzer**: Bounds time windows ($t - 15m$ to $t + 5m$) and formulates targeted investigation goals.
2. **Metrics Investigator**: Queries Prometheus golden signals (throughput, latency, error rate, DB pool saturation).
3. **Log Investigator**: Queries Loki error logs, extracts distributed trace IDs (`trace_id`), and identifies exceptions.
4. **Trace Investigator**: Queries Tempo traces, isolates root vs child spans, and identifies bottleneck spans and HTTP 5xx errors.
5. **Historical Context**: Correlates past incidents on PostgreSQL and identifies recurring deterministic fingerprints.
6. **Evidence Aggregator**: Merges empirical telemetry into verified `observed_facts` and `evidence_references` (`EV-...`).
7. **RCA Synthesizer**: Formulates evidence-backed causal hypotheses using Groq (`llama-3.3-70b-versatile`) or deterministic heuristic fallback.
8. **Evidence Validator**: Verifies citation referential integrity, detects contradictions, checks service attribution and window bounds, and calibrates heuristic confidence (`HIGH`, `MEDIUM`, `LOW`).

### Strictly READ-ONLY Guarantee
Agents exclusively inspect telemetry and generate hypotheses. Zero operational mutations, shell executions, container restarts, or database writes outside investigation persistence are permitted.

---

## 6. Quick Start & Operations

### Step 1: Start All Infrastructure (Docker Compose)
```bash
docker compose up -d --build --wait
```

### Step 2: Run Database Migrations
```bash
cd backend
uv sync --locked --extra dev
uv run alembic upgrade head
```

### Step 3: Start Backend API & Celery Worker
```bash
# Terminal 1: FastAPI API
cd backend
uv run uvicorn app.main:app --host 127.0.0.1 --reload --port 8000

# Terminal 2: Celery Worker
cd backend
uv run celery -A app.core.celery_app worker -l info

# Terminal 3: Celery Beat
cd backend
uv run celery -A app.core.celery_app beat -l info
```
Interactive API documentation is accessible at [http://localhost:8000/docs](http://localhost:8000/docs).

### Step 4: Start Frontend Web UI
```bash
cd frontend
npm ci
npm run dev -- --hostname 127.0.0.1
```
Open [http://localhost:3000](http://localhost:3000) for the Incidents Dashboard.

### Step 5: Start Autonomous Investigation
```bash
# Start investigation via REST API
curl -X POST http://localhost:8000/api/v1/incidents/{incident_id}/investigations \
  -H "Content-Type: application/json" \
  -d '{"trigger_type": "MANUAL"}' | jq .
```
Or click **"Start Autonomous Investigation"** in the Next.js incident drawer.

### Step 6: Propose and authorize controlled remediation

For the fault simulation, start the demo containers with `FAULT_INJECTION_ENABLED=true`. Set `REMEDIATION_OPERATOR_TOKEN` in `backend/.env` to a strong secret before starting the API. You can generate one with `openssl rand -hex 32`. Set `REMEDIATION_OPERATOR_ID` to the operator name to record in the audit log. If you upgraded from an older version, create a fresh plan for any unexecuted approvals after applying the migration.

```bash
# Propose an evidence-backed remediation plan
curl -X POST http://localhost:8000/api/v1/incidents/{incident_id}/remediation-plans \
  -H "Content-Type: application/json" | jq .

# Approve a specific allowlisted action (Mandatory Human Operator Gate)
curl -X POST http://localhost:8000/api/v1/remediation-plans/{plan_id}/actions/{action_id}/approve \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $REMEDIATION_OPERATOR_TOKEN" \
  -d '{"comment": "Approved following RCA review", "plan_version": 1}' | jq .
```
Use the **Remediation & Recovery** panel to review the action, enter the operator token, confirm execution, and track verification. For the curl example, set the shell variable `REMEDIATION_OPERATOR_TOKEN` to the same token as the API.

---

## 7. Automated Testing & Code Quality

The Python suites cover unit tests and integration tests with simulated dependencies. The backend also includes a live Redis test. Run the pipeline check separately against the running Compose stack.

```bash
# Run backend tests
cd backend
uv run --locked --extra dev pytest -v

# Run demo service tests
cd ../sample-services
uv run --locked --extra dev pytest -v

# Code Formatting & Linting
cd ../backend && uv run ruff check .
cd ../frontend && npm run lint && npm run build

# From sample-services, with the Compose stack running
cd ../sample-services && uv run --locked verify_pipeline.py
```

---

## 8. Documentation Index

### Phase 1–3 Foundations
- **Observability Audit Report:** [`docs/OBSERVABILITY_AUDIT.md`](docs/OBSERVABILITY_AUDIT.md)
- **Phase 3 Implementation Guide:** [`docs/PHASE_3_IMPLEMENTATION.md`](docs/PHASE_3_IMPLEMENTATION.md)

### Phase 4 Multi-Agent Investigation
- **Phase 4 Architecture Specification:** [`docs/PHASE_4_ARCHITECTURE.md`](docs/PHASE_4_ARCHITECTURE.md)
- **Phase 4 Implementation Guide:** [`docs/PHASE_4_IMPLEMENTATION.md`](docs/PHASE_4_IMPLEMENTATION.md)
- **Phase 4 Evaluation & Benchmark Report:** [`docs/PHASE_4_EVALUATION.md`](docs/PHASE_4_EVALUATION.md)
- **Phase 4 Independent Audit Report:** [`docs/PHASE_4_AUDIT_REPORT.md`](docs/PHASE_4_AUDIT_REPORT.md)
- **Phase 4 Remediation & Reliability Report:** [`docs/PHASE_4_REMEDIATION_REPORT.md`](docs/PHASE_4_REMEDIATION_REPORT.md)

### Phase 5 Controlled Remediation & Verified Recovery
- **Current repository audit and next steps** [PROJECT_AUDIT.md](docs/PROJECT_AUDIT.md)
- **Phase 5 Architecture Specification:** [`docs/PHASE_5_ARCHITECTURE.md`](docs/PHASE_5_ARCHITECTURE.md)
- **Phase 5 Implementation Guide:** [`docs/PHASE_5_IMPLEMENTATION.md`](docs/PHASE_5_IMPLEMENTATION.md)
- **Phase 5 Security Architecture & Policy Invariants:** [`docs/PHASE_5_SECURITY.md`](docs/PHASE_5_SECURITY.md)
- **Phase 5 Evaluation & Scenario Report:** [`docs/PHASE_5_EVALUATION.md`](docs/PHASE_5_EVALUATION.md)
- **Phase 5 Formal Completion Report:** [`docs/PHASE_5_COMPLETION_REPORT.md`](docs/PHASE_5_COMPLETION_REPORT.md)

---

## 9. License

OpsPilot is developed as an open-source portfolio project under the MIT License.
