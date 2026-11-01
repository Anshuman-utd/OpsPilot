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

## 2. Product Vision & Architecture

OpsPilot maintains a strict boundary between **observed facts** and **AI-generated hypotheses**. The platform never takes unverified actions against production environments and enforces Human-in-the-Loop (HITL) approval for all remediation.

### Architecture Workflow

```mermaid
flowchart TD
    subgraph Observability ["Telemetry & Observability (Phase 2)"]
        APP["Target Applications"] --> METRICS["Prometheus (Metrics)"]
        APP --> LOGS["Grafana Loki (Logs)"]
        APP --> TRACES["Grafana Tempo (Traces)"]
    end

    subgraph CoreEngine ["OpsPilot Core Platform (Phases 1, 3, 4)"]
        DETECTION["Detection Engine / Celery Workers"] --> INCIDENT["Incident Management API"]
        INCIDENT --> GRAPH["LangGraph SRE Workflow"]
        
        subgraph Agents ["Specialized AI Agents"]
            GRAPH --> MA["Metrics Agent"]
            GRAPH --> LA["Logs Agent"]
            GRAPH --> TA["Traces Agent"]
            MA & LA & TA --> RCA["RCA Synthesis Agent"]
        end

        RCA --> REMEDIATION["Remediation Planner"]
    end

    subgraph UserInterface ["Human-in-the-Loop (Phases 5 & 6)"]
        REMEDIATION --> HITL{"Human Review & Approval"}
        HITL -- "Approved" --> RUNNER["Safe Sandbox Runner"]
        RUNNER --> VERIFY["Verification Agent"]
        VERIFY --> DASHBOARD["Next.js Incident Dashboard"]
        HITL -- "Rejected" --> DASHBOARD
    end
```

---

## 3. Technology Stack

### Backend
- **Language / Runtime:** Python 3.11+
- **API Framework:** FastAPI
- **Data Validation & Settings:** Pydantic v2 & Pydantic Settings
- **ORM & Database Client:** SQLAlchemy 2.0 (asyncio) + `asyncpg`
- **Database Migrations:** Alembic (async)
- **Caching & Background Broker:** Redis (asyncio) & Celery
- **HTTP Client:** HTTPX
- **Package & Environment Manager:** `uv`
- **Testing & Quality:** `pytest`, `pytest-asyncio`, `ruff`

### Frontend
- **Framework:** Next.js 16 (App Router)
- **Language:** TypeScript
- **Styling:** Tailwind CSS
- **Code Quality:** ESLint

### Agentic AI *(Planned for Phase 4)*
- **Orchestration:** LangGraph
- **Abstractions:** LangChain
- **LLM Provider:** Groq (initial, provider-agnostic architecture)

### Infrastructure & Observability *(Phases 1, 2 & 7)*
- **Containers:** Docker & Docker Compose
- **Datastores:** PostgreSQL 16 (Alpine), Redis 7 (Alpine)
- **Metrics, Logs & Traces:** Prometheus, Grafana Loki, Grafana Tempo, OpenTelemetry *(Phase 2)*
- **Cloud Deployment:** AWS ECS/EKS *(Phase 7)*

---

## 4. Current Implementation Status & Roadmap

| Phase | Milestone | Status | Key Deliverables |
| :--- | :--- | :---: | :--- |
| **Phase 1** | **Project Foundation & Development Environment** | **COMPLETE** | Monorepo structure, FastAPI async backend, Next.js frontend, Docker Compose (PostgreSQL 16 & Redis 7), Alembic async migrations, health probes, automated pytest suite. |
| Phase 2 | Demo Microservices & Telemetry Instrumentation | *Planned* | Sample microservices (API Gateway, Order, Payment), OpenTelemetry, Prometheus, Loki, Tempo, synthetic fault injection. |
| Phase 3 | Deterministic Incident Detection & Lifecycle Engine | *Planned* | Service registry, incident data models, threshold-based detection, Celery background monitoring. |
| Phase 4 | LangGraph Multi-Agent Investigation & RCA Synthesis | *Planned* | Metrics, Logs, and Traces agents, LangGraph orchestration, evidence-backed RCA synthesis. |
| Phase 5 | Remediation Planning, HITL Approval & Verification | *Planned* | Structured remediation plans, human-in-the-loop review, safe sandboxed execution, verification agent. |
| Phase 6 | Next.js Incident Command Dashboard & Evaluation Suite | *Planned* | Incident timelines, agent execution graphs, post-mortem generation, synthetic evaluation benchmarks. |
| Phase 7 | Cloud Production Deployment on AWS | *Planned* | Infrastructure as Code (Terraform), container orchestration, production CI/CD pipelines. |

> *Note: AI agents, telemetry ingestion pipelines, and remediation executors are future roadmap phases. Phase 1 focuses exclusively on establishing a rock-solid, production-grade foundation.*

---

## 5. Repository Structure

```text
opspilot/
├── backend/                       # FastAPI backend application
│   ├── alembic/                   # Async database migrations
│   │   ├── versions/              # Migration revision scripts (.gitkeep)
│   │   ├── env.py                 # Async Alembic environment script
│   │   └── script.py.mako         # Migration template
│   ├── alembic.ini                # Alembic configuration
│   ├── app/
│   │   ├── api/                   # API routers and endpoints
│   │   │   └── health.py          # Liveness and readiness endpoints
│   │   ├── core/                  # Core infrastructure
│   │   │   ├── config.py          # Centralized Pydantic settings
│   │   │   ├── database.py        # Async SQLAlchemy engine & session factory
│   │   │   ├── redis.py           # Async Redis client & pool
│   │   │   └── celery_app.py      # Celery application initialization
│   │   └── main.py                # FastAPI application entrypoint & lifespan
│   ├── tests/                     # Automated pytest test suite
│   │   ├── conftest.py            # Test fixtures (async HTTP client)
│   │   ├── test_main.py           # App factory and root endpoint tests
│   │   ├── test_health.py         # Liveness and readiness probe tests
│   │   ├── test_database.py       # Database connection & session tests
│   │   └── test_redis.py          # Redis client & ping tests
│   ├── pyproject.toml             # Python dependencies and tool configuration
│   ├── uv.lock                    # Deterministic package lockfile
│   ├── .env.example               # Backend environment template
│   └── README.md                  # Backend overview
├── frontend/                      # Next.js frontend application
│   ├── src/
│   │   ├── app/
│   │   │   ├── globals.css        # Tailwind CSS styles
│   │   │   ├── layout.tsx         # Root HTML layout and metadata
│   │   │   └── page.tsx           # OpsPilot landing & system overview
│   │   └── components/
│   │       └── ApiStatusIndicator.tsx # Client-side real-time health indicator
│   ├── package.json               # Frontend dependencies & scripts
│   ├── tsconfig.json              # TypeScript configuration with @/* alias
│   ├── eslint.config.mjs          # ESLint configuration
│   └── .env.example               # Frontend environment template
├── docs/                          # Architecture & design documentation
│   └── phase1-overview.md         # Phase 1 technical specification
├── docker-compose.yml             # Local infrastructure (PostgreSQL 16, Redis 7)
├── .gitignore                     # Monorepo gitignore rules
└── README.md                      # Master project documentation
```

---

## 6. Prerequisites

Ensure you have the following tools installed:
- **Python**: `>= 3.11` (Python 3.11.x recommended)
- **uv**: Modern, fast Python package manager (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Node.js**: `>= 18.18.0` (Node 20+ or 22 LTS recommended) and `npm`
- **Docker & Docker Compose**: Docker Desktop or Docker Engine

---

## 7. Local Development Setup

Follow these steps to set up and run OpsPilot locally:

### Step 1: Start Backing Services (Docker Compose)

Start PostgreSQL 16 and Redis 7 in detached mode:

```bash
docker-compose up -d
```

Verify that both containers are running and healthy:
```bash
docker-compose ps
```

To stop backing services:
```bash
docker-compose down
```

### Step 2: Backend Setup & Launch

```bash
cd backend

# 1. Create virtual environment and install all dependencies (including dev)
uv sync --extra dev

# 2. Create local environment configuration
cp .env.example .env

# 3. Apply database migrations
uv run alembic upgrade head

# 4. Start the FastAPI development server
uv run uvicorn app.main:app --reload --port 8000
```

The backend API will be available at:
- **Interactive OpenAPI Docs (Swagger):** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Alternative API Docs (ReDoc):** [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Root Metadata:** [http://localhost:8000/](http://localhost:8000/)
- **Liveness Probe:** [http://localhost:8000/health/live](http://localhost:8000/health/live)
- **Readiness Probe:** [http://localhost:8000/health/ready](http://localhost:8000/health/ready)

### Step 3: Frontend Setup & Launch

In a separate terminal window:

```bash
cd frontend

# 1. Install dependencies
npm install

# 2. Create local environment configuration
cp .env.example .env.local

# 3. Start Next.js development server
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser. The landing dashboard will connect to the backend and display live infrastructure status.

---

## 8. Health & Readiness API Endpoints

OpsPilot implements Kubernetes-compliant health probes:

| Endpoint | Method | Purpose | Typical Response |
| :--- | :---: | :--- | :--- |
| `/` | `GET` | Service metadata, status, and API documentation links | `200 OK` |
| `/health` | `GET` | Basic process health check | `200 OK` |
| `/health/live` | `GET` | Kubernetes liveness probe | `200 OK` |
| `/health/ready` | `GET` | Backing dependency verification (PostgreSQL & Redis) | `200 OK` (All healthy) / `503 Service Unavailable` (Degraded) |

#### Sample Readiness Response (200 OK)
```json
{
  "status": "ready",
  "timestamp": "2026-11-01T18:05:00.000000Z",
  "components": {
    "database": {
      "status": "healthy",
      "details": { "latency_ms": 1.15 }
    },
    "redis": {
      "status": "healthy",
      "details": { "latency_ms": 0.42 }
    }
  }
}
```

---

## 9. Automated Testing & Code Quality

### Backend Automated Tests
OpsPilot includes a deterministic pytest suite with mocked async database and Redis fixtures:

```bash
cd backend
uv run pytest -v
```

### Backend Linting & Formatting
Enforced using Ruff (PEP 8, PyUpgrade, Bugbear, Flake8-Import-Conventions):

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
```

To auto-format:
```bash
uv run ruff format .
```

### Frontend Linting & Build Verification
```bash
cd frontend
npm run lint
npm run build
```

---

## 10. Environment Variables Reference

### Backend (`backend/.env.example`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `APP_NAME` | `OpsPilot` | Name of the application |
| `APP_ENV` | `development` | Deployment environment (`development`, `staging`, `production`) |
| `DEBUG` | `true` | Enable debug logging and verbose exceptions |
| `HOST` | `0.0.0.0` | Bind host for Uvicorn |
| `PORT` | `8000` | Bind port for Uvicorn |
| `DATABASE_URL` | `postgresql+asyncpg://opspilot:opspilot_dev@localhost:5432/opspilot` | PostgreSQL async connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `LLM_PROVIDER` | `groq` | Initial LLM provider *(Phase 4)* |
| `LLM_MODEL` | `llama-3.3-70b-versatile` | Target LLM model *(Phase 4)* |
| `GROQ_API_KEY` | `""` | API key for Groq *(Optional in Phase 1)* |

### Frontend (`frontend/.env.example`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend API base URL |
| `NEXT_PUBLIC_APP_NAME` | `OpsPilot` | Application title display |
| `NEXT_PUBLIC_APP_ENV` | `development` | Environment mode display |

---

## 11. Troubleshooting Guide

### 1. Database Connection Refused
- **Symptom:** `/health/ready` returns `503` with `"database": {"status": "unhealthy"}`.
- **Fix:** Ensure Docker Compose is running: `docker-compose up -d`. Check logs with `docker-compose logs postgres`. Verify port `5432` is not occupied by another local PostgreSQL service.

### 2. Redis Connection Refused
- **Symptom:** `/health/ready` returns `503` with `"redis": {"status": "unhealthy"}`.
- **Fix:** Ensure Redis container is running: `docker-compose ps`. Test Redis directly with `docker exec -it opspilot-redis redis-cli ping`.

### 3. Port Conflicts
- If port `8000` (FastAPI) or `3000` (Next.js) is already in use:
  - Start FastAPI on another port: `uv run uvicorn app.main:app --port 8080`
  - Update `NEXT_PUBLIC_API_URL` in `frontend/.env.local` to match the new port.

### 4. uv Dependency Resolution
- If virtual environment needs a clean rebuild:
  ```bash
  cd backend
  rm -rf .venv
  uv sync --extra dev
  ```

---

## 12. Security Considerations

- **Zero Committed Secrets:** All credential strings and secret keys are stored in uncommitted `.env` / `.env.local` files, strictly ignored in git.
- **Frontend / Backend Boundary:** LLM keys and infrastructure secrets never reach the browser or client-side code.
- **Sanitized Health Probes:** Error messages in production environments can be masked to prevent information leakage while maintaining observability.

---

## 13. License

OpsPilot is developed as an open-source portfolio project under the MIT License.
