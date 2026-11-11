# OpsPilot — Phase 4 Implementation Guide

## 1. Overview

Phase 4 implements autonomous multi-agent incident investigation and Root Cause Analysis (RCA) using **LangGraph**, **FastAPI**, **PostgreSQL**, **Celery**, and **Next.js**.

---

## 2. Database Schema & Alembic Migration

### Migration: `003_create_investigation_tables.py`
Chains from `002_partial_unique_index` to create the following persistence tables:

### 2.1 `investigations` Table
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `VARCHAR(36)` | Primary Key | UUID identifier |
| `incident_id` | `VARCHAR(36)` | Foreign Key (`incidents.id`) ON DELETE CASCADE | Associated incident |
| `status` | `VARCHAR(32)` | Indexed, Default `'PENDING'` | `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED` |
| `trigger_type` | `VARCHAR(32)` | Default `'MANUAL'` | `MANUAL`, `AUTOMATIC` |
| `started_at` | `TIMESTAMP WITH TIME ZONE` | Nullable | Execution start timestamp |
| `completed_at` | `TIMESTAMP WITH TIME ZONE` | Nullable | Execution finish timestamp |
| `model_provider` | `VARCHAR(50)` | Nullable | e.g. `'groq'`, `'heuristic_fallback'` |
| `model_name` | `VARCHAR(100)` | Nullable | e.g. `'llama-3.3-70b-versatile'` |
| `total_duration_ms` | `FLOAT` | Nullable | Total workflow execution time in milliseconds |
| `summary` | `TEXT` | Nullable | Executive summary of findings |
| `error_message` | `TEXT` | Nullable | Error details if investigation failed |
| `final_report` | `JSONB` / `JSON` | Nullable | Complete structured `FinalRCAReport` |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | Indexed | Record creation timestamp |
| `updated_at` | `TIMESTAMP WITH TIME ZONE` | Nullable | Last update timestamp |

### 2.2 `investigation_agent_runs` Table
Captures execution telemetry and outputs of individual specialized agents:
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `id` | `VARCHAR(36)` | Primary Key | Run identifier |
| `investigation_id` | `VARCHAR(36)` | Foreign Key (`investigations.id`) ON DELETE CASCADE | Parent investigation |
| `agent_name` | `VARCHAR(100)` | Indexed | `incident_analyzer`, `metrics_agent`, `log_agent`, `trace_agent`, `historical_agent`, `aggregator`, `rca_agent`, `validator_agent` |
| `status` | `VARCHAR(32)` | Indexed | `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `SKIPPED` |
| `duration_ms` | `FLOAT` | Nullable | Agent run duration |
| `findings` | `JSONB` / `JSON` | Nullable | Structured findings payload |
| `error_message` | `TEXT` | Nullable | Agent error message if failed |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | Indexed | Record creation timestamp |

---

## 3. Asynchronous Celery Worker Architecture

Investigation workflows are non-blocking and execute in Celery background workers:

```
FastAPI (POST /api/v1/incidents/{id}/investigations)
  --> Creates Investigation record (status: PENDING)
  --> Dispatches Celery task: run_investigation_task.delay(investigation_id)
  --> Returns 202 Accepted immediately to client

Celery Worker (run_investigation_task):
  --> Transitions status to RUNNING
  --> Runs LangGraph workflow (run_investigation_workflow)
  --> Writes FinalRCAReport and agent runs to PostgreSQL
  --> Transitions status to COMPLETED (or FAILED on error)
```

Task definition in `backend/app/workers/investigation_tasks.py`:
- Task name: `app.workers.investigation_tasks.run_investigation_task`
- Concurrency-safe: Runs outside active HTTP request loops, avoiding connection pool starvation.

---

## 4. REST API Endpoints

| Method | Endpoint | Description | Status Code |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/incidents/{incident_id}/investigations` | Start autonomous investigation | `202 Accepted` |
| `GET` | `/api/v1/incidents/{incident_id}/investigations` | List chronological investigations for an incident | `200 OK` |
| `GET` | `/api/v1/investigations/{investigation_id}` | Retrieve full investigation detail & final report | `200 OK` |
| `GET` | `/api/v1/investigations/{investigation_id}/agents` | Retrieve individual agent execution telemetry | `200 OK` |
| `POST` | `/api/v1/investigations/{investigation_id}/retry` | Retry failed or cancelled investigation | `202 Accepted` |

---

## 5. Telemetry Query Tools (`backend/app/investigation/tools/`)

1. **`PrometheusInvestigationTool`**:
   - `get_service_error_rate(service, start, end)`
   - `get_service_p95_latency(service, start, end)`
   - `get_service_request_rate(service, start, end)`
   - `get_payment_failure_ratio(start, end)`
   - `get_db_pool_saturation(service, start, end)`
   - `get_downstream_latency(service, target_service, start, end)`: Queries `downstream_request_duration_seconds_bucket` with `target` label filter.
   - `compare_windows(query, current_window, baseline_window)`
   - Auto-computes `overall_trend` (`increasing`, `decreasing`, `stable`) and builds direct deep links to Prometheus Graph.

2. **`LokiInvestigationTool`**:
   - `search_error_logs(service, start, end, limit=30)`
   - `search_logs_by_keyword(service, keyword, start, end, limit=20)`
   - `get_logs_for_trace(trace_id, limit=30)`
   - Auto-extracts distributed trace IDs (`trace_id`) from log streams and generates Grafana Explore links.

3. **`TempoInvestigationTool`**:
   - `search_recent_traces(service, start, end, limit=5, error_only=True)`
   - `get_trace_details(trace_id)`
   - Parses OTel span hierarchies, isolates root span, identifies longest bottleneck span, and extracts failed HTTP spans.

4. **`IncidentHistoryTool`**:
   - `get_historical_incidents(service_name, lookback_days=14, limit=5)`
   - `get_similar_incidents_by_fingerprint(fingerprint, limit=5)`
   - Recovers past resolution durations and previous RCA summaries to correlate recurring failures.

---

## 6. Frontend Experience (`frontend/src/components/IncidentsDashboard.tsx`)

The Next.js incident drawer now features a full autonomous investigation panel:
- **"Start Autonomous Investigation" Button**: Dispatches background multi-agent investigation with instant feedback.
- **Multi-Agent Swarm Pipeline**: Live status pills for all 8 agents (`Incident Analyzer`, `Metrics Investigator`, `Log Investigator`, `Trace Investigator`, `Historical Context`, `Evidence Aggregator`, `RCA Synthesizer`, `Evidence Validator`).
- **RCA Report Card**:
  - Executive summary with calibrated confidence badge (`HIGH`, `MEDIUM`, `LOW`).
  - Empirical observed facts list.
  - Root cause hypotheses cards with confidence score bars, affected components, and assumptions.
  - Expandable evidence cards with query previews and one-click deep links to Prometheus, Loki, and Tempo.
  - Past investigation runs selector for comparative analysis.

