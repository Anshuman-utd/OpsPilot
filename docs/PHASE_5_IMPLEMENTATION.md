# OpsPilot — Phase 5: Controlled Remediation, Execution & Recovery Verification
## Implementation Guide

---

## 1. Overview & Module Structure

Phase 5 introduces the `app.remediation` package within `backend/app/`, fully integrated with existing Phase 1–4 foundations:

```
backend/app/remediation/
├── __init__.py           # Package exports for models, schemas, and services
├── models.py             # SQLAlchemy ORM models (plans, actions, approvals, executions, verification)
├── schemas.py            # Pydantic v2 validation and API response schemas
├── registry.py           # ActionDefinition registry, static allowlists, and microservice handlers
├── policy.py             # Deterministic SafetyPolicyEngine (12 safety rules)
├── prompts.py            # System prompts with strict untrusted data boundaries
├── planner.py            # RemediationPlanner (LLM structured synthesis + heuristic fallback)
├── service.py            # Domain service managing plan formulation, approvals, rejections, and queries
├── executor.py           # RemediationExecutor with distributed Redis locking and safety gates
├── verifier.py           # RecoveryVerificationEngine (multi-cycle Prometheus telemetry evaluation)
└── router.py             # FastAPI REST router mounted at /api/v1/...
```

Supporting worker and migration modules:
- `backend/app/workers/remediation_tasks.py`: Celery background tasks for execution and verification.
- `backend/alembic/versions/004_create_remediation_tables.py`: Database migration script for Phase 5 tables.
- `frontend/src/components/RemediationDrawerSection.tsx`: Next.js interactive UI component for remediation drawer.

---

## 2. Database Schema & Alembic Migration

Migration `004_create_remediation_tables.py` establishes 6 core tables with strict foreign key constraints and indexes:

### 2.1. `remediation_plans`
| Column | Type | Constraints / Description |
|---|---|---|
| `id` | VARCHAR(36) | Primary Key (UUIDv4) |
| `incident_id` | VARCHAR(36) | Foreign Key (`incidents.id`, ON DELETE CASCADE), Indexed |
| `investigation_id` | VARCHAR(36) | Foreign Key (`investigations.id`, ON DELETE SET NULL), Nullable |
| `status` | VARCHAR(32) | Enum: `DRAFT`, `PENDING_APPROVAL`, `APPROVED`, `REJECTED`, `EXPIRED`, `EXECUTING`, `VERIFYING`, `SUCCEEDED`, `FAILED`, `CANCELLED` |
| `version` | INTEGER | Version number (defaults to 1; incremented upon re-planning) |
| `summary` | TEXT | Executive summary of proposed remediation strategy |
| `root_cause_hypothesis` | JSONB / JSON | Serialized target root cause hypothesis |
| `supporting_evidence_ids` | JSONB / JSON | Telemetry evidence IDs directly supporting the plan |
| `contradicting_evidence_ids` | JSONB / JSON | Contradicting or cautionary evidence IDs |
| `risk_assessment` | JSONB / JSON | Risk rating, blast radius, and downtime assessment |
| `assumptions` | JSONB / JSON | Declared assumptions |
| `limitations` | JSONB / JSON | Gaps in telemetry or automated capability |
| `expires_at` | TIMESTAMP WITH TIME ZONE | Approval window expiration timestamp |
| `created_at` / `updated_at` | TIMESTAMP WITH TIME ZONE | Audit timestamps |

### 2.2. `remediation_actions`
| Column | Type | Constraints / Description |
|---|---|---|
| `id` | VARCHAR(36) | Primary Key (UUIDv4) |
| `plan_id` | VARCHAR(36) | Foreign Key (`remediation_plans.id`, ON DELETE CASCADE), Indexed |
| `action_type` | VARCHAR(64) | Must match allowlisted `ACTION_REGISTRY` entry |
| `target_service` | VARCHAR(64) | Restricted to `order-service`, `payment-service`, `api-gateway` |
| `environment` | VARCHAR(32) | Restricted to `demo`, `local`, `development` |
| `parameters` | JSONB / JSON | Validated action parameters schema |
| `rationale` | TEXT | Justification grounded in investigation evidence |
| `expected_outcome` | TEXT | Predicted impact on incident metrics |
| `risk_level` | VARCHAR(16) | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |
| `reversible` | BOOLEAN | Whether action is safely reversible |
| `preconditions` | JSONB / JSON | Required preconditions |
| `verification_conditions` | JSONB / JSON | Target metric, threshold, and required healthy cycles |
| `status` | VARCHAR(32) | Enum: `PROPOSED`, `PENDING_APPROVAL`, `APPROVED`, `REJECTED`, `EXECUTING`, `EXECUTED`, `FAILED`, `VERIFYING`, `VERIFIED`, `CANCELLED` |

### 2.3. `remediation_approvals`
| Column | Type | Constraints / Description |
|---|---|---|
| `id` | VARCHAR(36) | Primary Key (UUIDv4) |
| `plan_id` | VARCHAR(36) | Foreign Key (`remediation_plans.id`, ON DELETE CASCADE), Indexed |
| `action_id` | VARCHAR(36) | Foreign Key (`remediation_actions.id`, ON DELETE CASCADE), Indexed |
| `decision` | VARCHAR(16) | `APPROVED`, `REJECTED`, `CANCELLED` |
| `approver_id` | VARCHAR(128) | Authenticated operator identity |
| `comment` | TEXT | Mandatory operator justification / rationale |
| `plan_version` | INTEGER | Immutable plan version approved |
| `expires_at` | TIMESTAMP WITH TIME ZONE | Expiration boundary for approval |
| `decided_at` | TIMESTAMP WITH TIME ZONE | Decision timestamp |

### 2.4. `remediation_executions`
| Column | Type | Constraints / Description |
|---|---|---|
| `id` | VARCHAR(36) | Primary Key (UUIDv4) |
| `plan_id` | VARCHAR(36) | Foreign Key (`remediation_plans.id`, ON DELETE CASCADE), Indexed |
| `action_id` | VARCHAR(36) | Foreign Key (`remediation_actions.id`, ON DELETE CASCADE), Indexed |
| `approval_id` | VARCHAR(36) | Foreign Key (`remediation_approvals.id`, ON DELETE RESTRICT), Indexed |
| `idempotency_key` | VARCHAR(128) | Unique key: `exec-{action_id}-v{plan_version}` (Indexed, Unique) |
| `status` | VARCHAR(32) | `PENDING`, `EXECUTING`, `SUCCEEDED`, `FAILED`, `VERIFYING`, `VERIFICATION_FAILED`, `RECOVERED` |
| `started_at` / `completed_at` | TIMESTAMP WITH TIME ZONE | Execution lifecycle timestamps |
| `duration_ms` | FLOAT | Execution duration in milliseconds |
| `execution_output` | JSONB / JSON | Raw response from microservice handler |
| `error_message` | TEXT | Error details if handler failed |

### 2.5. `remediation_verification_runs`
| Column | Type | Constraints / Description |
|---|---|---|
| `id` | VARCHAR(36) | Primary Key (UUIDv4) |
| `execution_id` | VARCHAR(36) | Foreign Key (`remediation_executions.id`, ON DELETE CASCADE), Indexed |
| `status` | VARCHAR(32) | `VERIFYING`, `RECOVERED`, `STILL_DEGRADED`, `REGRESSION_DETECTED`, `TELEMETRY_UNAVAILABLE` |
| `consecutive_healthy_count` | INTEGER | Current count of consecutive healthy cycles |
| `required_healthy_count` | INTEGER | Target consecutive healthy count (default: 3) |
| `observations` | JSONB / JSON | Array of all cycle observations, timestamps, and metric values |
| `summary` | TEXT | Telemetry verification summary |

### 2.6. `remediation_audit_events`
| Column | Type | Constraints / Description |
|---|---|---|
| `id` | VARCHAR(36) | Primary Key (UUIDv4) |
| `incident_id` | VARCHAR(36) | Foreign Key (`incidents.id`, ON DELETE CASCADE), Indexed |
| `investigation_id` | VARCHAR(36) | Foreign Key (`investigations.id`, ON DELETE SET NULL), Nullable |
| `plan_id` | VARCHAR(36) | Foreign Key (`remediation_plans.id`, ON DELETE SET NULL), Nullable |
| `action_id` | VARCHAR(36) | Foreign Key (`remediation_actions.id`, ON DELETE SET NULL), Nullable |
| `execution_id` | VARCHAR(36) | Foreign Key (`remediation_executions.id`, ON DELETE SET NULL), Nullable |
| `event_type` | VARCHAR(64) | Immutable event type (`PLAN_PROPOSED`, `POLICY_EVALUATED`, `ACTION_APPROVED`, `EXECUTION_STARTED`, `RECOVERY_VERIFIED`, etc.) |
| `actor` | VARCHAR(128) | Identity of caller or engine component |
| `details` | JSONB / JSON | Event payload |
| `created_at` | TIMESTAMP WITH TIME ZONE | Timestamp of event |

---

## 3. Action Registry & Microservice Handlers (`registry.py`)

All actions execute strictly via defined Python async handlers calling the demo microservices' debug endpoints:

```python
async def handle_disable_fault_injection(
    service_name: str, parameters: dict[str, Any], timeout_seconds: float
) -> dict[str, Any]:
    fault_type = parameters.get("fault_type")
    base_url = _get_service_base_url(service_name)
    url = f"{base_url}/debug/faults/{fault_type}/disable"

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(url)
        resp.raise_for_status()
        return resp.json()
```

Target service URLs are read dynamically from settings:
- `order-service`: `http://localhost:8002` (configurable via `ORDER_SERVICE_URL`)
- `payment-service`: `http://localhost:8003` (configurable via `PAYMENT_SERVICE_URL`)
- `api-gateway`: `http://localhost:8001` (configurable via `API_GATEWAY_URL`)

---

## 4. Deterministic Safety Policy Engine (`policy.py`)

The Safety Policy Engine runs two explicit evaluation passes:

1. **Pre-Proposal Pass (`evaluate_proposal`)**:
   - Asserts action is registered, environment is allowlisted, service is allowlisted, parameters match Pydantic schema, and target incident is open.
   - Verifies supporting evidence IDs exist in Phase 4 investigation results.
   - If policy rejects, the action is marked `ActionStatus.PROPOSED` (cannot be approved directly without operator review).

2. **Pre-Execution Gate (`evaluate_execution_readiness`)**:
   - Validates that an explicit approval record exists.
   - Validates that approval has not expired (`approval.expires_at >= now`).
   - Validates that plan version matches approval version (`approval.plan_version == plan.version`).
   - Validates that action decision is `APPROVED`.
   - Validates that action has not already executed (`ActionStatus.EXECUTED`, `ActionStatus.VERIFIED`).
   - If any rule fails, execution is immediately aborted and marked `ExecutionStatus.FAILED`.

---

## 5. Remediation Service & REST APIs (`service.py`, `router.py`)

### Exposed Endpoints:
- `POST /api/v1/incidents/{incident_id}/remediation-plans`: Formulates and persists a new plan. Returns HTTP 201.
- `GET /api/v1/incidents/{incident_id}/remediation-plans`: Lists all plans created for an incident. Returns HTTP 200.
- `GET /api/v1/remediation-plans/{plan_id}`: Retrieves complete plan details with child actions and executions. Returns HTTP 200.
- `POST /api/v1/remediation-plans/{plan_id}/actions/{action_id}/approve`: Submits human approval, records approval, creates execution, and enqueues Celery task. Returns HTTP 202 Accepted.
- `POST /api/v1/remediation-plans/{plan_id}/actions/{action_id}/reject`: Submits human rejection with mandatory rationale. Transitions plan to `REJECTED`. Returns HTTP 200.
- `POST /api/v1/remediation-plans/{plan_id}/cancel`: Operator cancels a pending plan. Returns HTTP 200.
- `GET /api/v1/remediation-plans/{plan_id}/executions`: Lists all execution records for a plan. Returns HTTP 200.
- `GET /api/v1/remediation-executions/{execution_id}`: Detailed execution status with verification runs. Returns HTTP 200.
- `GET /api/v1/remediation-plans/{plan_id}/audit-events`: Chronological immutable audit event history. Returns HTTP 200.

---

## 6. Asynchronous Celery Tasks (`remediation_tasks.py`)

Two specialized Celery tasks manage background execution without blocking API threads:

1. `run_remediation_execution_task`:
   - Runs `RemediationExecutor.execute_action(execution_id)`.
   - Acquires service lock.
   - Invokes microservice HTTP handler.
   - Transitions to `VERIFYING` on success.
   - Dispatches `run_remediation_verification_task.delay(execution_id)`.

2. `run_remediation_verification_task`:
   - Runs `RecoveryVerificationEngine.verify_recovery(execution_id)`.
   - Queries live Prometheus metric endpoint across consecutive cycles.
   - Upon recovery, updates execution to `RECOVERED`, plan to `SUCCEEDED`, and auto-resolves the incident.

---

## 7. Frontend Integration (`RemediationDrawerSection.tsx`)

The React component `RemediationDrawerSection.tsx` mounts inside the slide-out incident drawer:
- **Interactive Stepper**: Displays real-time progress across 5 stages (`Formulated` $\to$ `Approved` $\to$ `Executed` $\to$ `Verifying` $\to$ `Restored`).
- **Human Approval Dialog**: Modal requiring operator username, justification comment, and checkbox confirmation before execution.
- **Rejection Dialog**: Modal capturing operator rejection rationale.
- **Verification Telemetry Panel**: Live multi-cycle observation table showing cycle number, timestamp, observed value, and healthy status badge.
- **Telemetry Alert Banner**: Displays explicit warning when Prometheus is unreachable (`TELEMETRY_UNAVAILABLE`).
- **Audit Trail**: Collapsible chronological audit log displaying all Phase 5 events.

