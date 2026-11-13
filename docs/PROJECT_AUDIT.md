# OpsPilot repository audit and next steps

Reviewed on 13 November 2026. OpsPilot is a working local SRE demo. The repository contains a FastAPI platform, a Next.js dashboard, three demo microservices, and a Compose observability stack. Its current controls are designed for a single operator on a trusted machine.

## Findings and changes

| Area | Finding and fix |
| --- | --- |
| Backend and workers | The documented Celery entry point omitted the task modules, and worker tasks reused async connection pools across event loops. The app now registers all four tasks and the Beat schedule. Each worker task closes its PostgreSQL and Redis resources before its event loop closes. Scheduled detection now requires Redis and releases its lease only if it still owns the lock. |
| Detection and investigation | Error ratios could disappear when no error series existed despite positive traffic. PromQL now supplies zero for that case and preserves missing results when there is no traffic. Investigations can reach the deterministic RCA fallback without a Groq key, use the requested model override when invoking the provider, skip duplicate work, and record a failed enqueue as retryable from the UI. Agent timing fields stay empty until measured. |
| Remediation | The API requires a configured operator bearer token for approvals, rejections, and cancellations. It records the server-configured identity and binds approval to a digest of the action, its parameters, and its recovery criteria. The worker rechecks approval after acquiring the Redis lease and serializes the execution claim with plan changes. It retries lock contention before dispatch and bounds handler execution time. The planner uses the actual structured LLM method. Demo handlers propagate failed HTTP responses. |
| Recovery verification | The verifier uses configured intervals and timeouts, evaluates telemetry at the current time, and requires consecutive healthy observations. An empty result remains unavailable. The underlying two-minute rate windows can overlap, so consecutive polls represent fresh evaluations of rolling signals. |
| Database | Two Alembic revisions add approval content hashes and five indexes missing from migration history. Apply them with `uv run alembic upgrade head` from `backend`. Create fresh plans for older approvals that have yet to execute. |
| Frontend | Approval and rejection dialogs accept the operator token and surface request errors where the operator can see them. The recovery table reads metric names and values from the actual API fields and labels unavailable telemetry. |
| Demo services and observability | Compose publishes ports on loopback, uses consistent demo database configuration, and waits for dependent microservice health checks. Alloy limits discovery to OpsPilot containers. The pipeline check returns a failure exit code when a service is unavailable, the order fails to confirm, or Prometheus misses a demo service. |

## Verification

- All 155 backend and 27 demo service tests passed. The Redis contention test ran against the local Redis container. Ruff, frontend ESLint, TypeScript, and the production build passed.
- A temporary PostgreSQL database passed migration from an empty database, downgrade to revision 004, and re-upgrade. `alembic check` found no model drift.
- The updated Compose stack ran locally with loopback-only published ports. The live pipeline created a confirmed order and confirmed Prometheus was scraping all three services. Loki and Tempo needed additional startup time after their containers reported running.
- A recent order log in Loki linked to a Tempo trace spanning the gateway, order, and payment services.
- A real Celery worker ran two consecutive detection jobs against PostgreSQL, Redis, and Prometheus. A seeded incident went through the API, deterministic RCA, operator-token approval, a live payment-service fault-disable request, and three healthy Prometheus observations to reach `RECOVERED`.
- The live worker check used synthetic demo data and a temporary platform database. It exercised the deterministic provider path. Live Groq responses and concurrent execution across multiple worker processes still need coverage.

## Prioritized plan

1. **Automate the local demo in CI.** Run the existing suites, frontend build, migrations and `alembic check`, then a Compose smoke test that starts from fresh volumes. Add a timed payment fault, generate sustained order traffic, and assert that detection opens the incident, an operator approves the action, and recovery resolves it. Assert Loki and Tempo readiness before smoke tests. Repeat with a failed telemetry source and a second worker.
2. **Recover from interrupted jobs.** Add durable task dispatch and reconciliation for stale pending, executing, and verifying records. Persist progress so a worker restart can resume verification. Give downstream actions explicit idempotency keys and test broker failure or process loss before and after dispatch. Enforce the concurrency settings and route manual and scheduled detection through shared coordination.
3. **Require stronger evidence of recovery.** Validate the verification signal and threshold against the incident type, require a minimum volume of post-action traffic, and confirm the specific incident condition has cleared before resolving it. Keep missing telemetry visible and test recurrence after a reported recovery.
4. **Tighten investigation and UI contracts.** Decide whether to implement automatic investigation or remove its unused setting. Measure actual agent timings and expose queued, running, and degraded states. Add browser coverage for polling, stale selections, and approval failures. Validate Groq's configured model with sanitized demo evidence before depending on it.
5. **Establish shared-environment controls before exposing the stack.** Replace the single local token with per-user authentication and role-based authorization. Protect incident mutations and fault-control endpoints, add audit retention and restricted write access, and require TLS and deployment-level service isolation. The registry's legacy pool and configuration action IDs currently map to demo fault endpoints.
