# OpsPilot — Phase 4 Evaluation & Benchmark Report

## 1. Evaluation Methodology

The Phase 4 evaluation suite validates the accuracy, factual consistency, confidence calibration, and fault tolerance of the autonomous investigation workflow.

The evaluation runner (`app/investigation/evaluation/evaluator.py`) executes 5 automated checks against each benchmark scenario:

1. **Primary Cause Attribution**: Does the top-ranked hypothesis identify the true ground-truth root cause?
2. **Confidence Calibration**: Is confidence accurately rated (`HIGH`, `MEDIUM`, `LOW`) according to the deterministic multi-source evidence criteria?
3. **Evidence Fidelity & Citation Integrity**: Does every cited evidence reference (`EV-...`) correspond to an actual retrieved telemetry observation? Are phantom evidence IDs stripped?
4. **Required Sources Coverage**: Did the investigation retrieve and cite the necessary telemetry sources (`METRIC`, `LOG`, `TRACE`, `INCIDENT_HISTORY`)?
5. **Forbidden Claims Avoidance**: Did the model avoid asserting speculative or unobserved hardware/corruption claims?

> [!NOTE]
> **Execution Mode Disclosure:** All 4 benchmark scenarios in `backend/tests/test_investigation_evaluation.py` execute against **deterministic mock fixtures and simulated telemetry payloads** in CI. They provide reproducible contract verification and do **not** claim live end-to-end telemetry validation against running microservices. Live telemetry interaction requires the Docker Compose observability stack and is evaluated separately.

---

## 2. Benchmark Scenarios & Results

| Scenario | Service | Incident Type | Ground Truth Cause | Expected Confidence | Accuracy Score | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Scenario A: Payment Failure Cascade** | `order-service` | `high_http_5xx_rate` | Downstream payment gateway decline & timeout | `HIGH` | **100%** | **PASSED** |
| **Scenario B: Database Connection Pool Exhaustion** | `order-service` | `high_p95_latency` | Active database checked-out pool saturation | `HIGH` | **100%** | **PASSED** |
| **Scenario C: Partial Telemetry Outage** | `payment-service` | `payment_failure` | Payment gateway failure (with logs/traces offline) | `LOW` | **100%** | **PASSED** |
| **Scenario D: Missing LLM API Key (Offline Fallback)** | `order-service` | `high_http_5xx_rate` | Elevated HTTP 5xx error rate (heuristic fallback) | `MEDIUM` | **100%** | **PASSED** |

### Benchmark Pass Rate: 100% (4 / 4 Scenarios Passed)

---

## 3. Scenario Analysis

### 3.1 Scenario A: Payment Failure Cascade
- **Trigger**: `order-service` HTTP 5xx error rate reaches 22% (threshold: 5%).
- **Telemetry Retrieved**:
  - `EV-METRIC-01`: `payments_processed_total{status="FAILED"}` increased by 28%.
  - `EV-TRACE-01`: Tempo trace shows `payment-service::POST /payments/charge` failed with HTTP 503 (duration: 1540ms).
- **Validation**: Both `METRIC` and `TRACE` telemetry corroborated the hypothesis. The Evidence Validator calibrated confidence to **HIGH** (heuristic score: 0.85).

### 3.2 Scenario B: Database Connection Pool Exhaustion
- **Trigger**: `order-service` P95 latency breaches SLA at 2.45s (threshold: 1.0s).
- **Telemetry Retrieved**:
  - `EV-METRIC-01`: `db_pool_checked_out` reaches 92% of capacity.
  - `EV-LOG-01`: Loki retrieved error logs: `"Database connection pool timed out while acquiring connection"`.
- **Validation**: Corroborated by `METRIC` + `LOG`. Calibrated to **HIGH** confidence (heuristic score: 0.85).

### 3.3 Scenario C: Partial Telemetry Outage (Fault Resilience)
- **Fault Injected**: Loki and Tempo endpoints drop connections or return 502 Bad Gateway.
- **Agent Behavior**:
  - `log_agent` and `trace_agent` isolate the failure, record error messages in `agent_errors`, and do not crash the workflow.
  - `metrics_agent` continues querying Prometheus successfully.
  - `validator_agent` detects that only a single source (`METRIC`) is available and downgrades confidence to **LOW** (heuristic score: 0.30 to 0.40).
- **Validation**: Zero unhandled exceptions. Telemetry limitations explicitly reported in `investigation_limitations`.

### 3.4 Scenario D: Missing LLM API Key (Deterministic Fallback)
- **Condition**: Backend initialized without `GROQ_API_KEY`.
- **Agent Behavior**:
  - `rca_agent` detects unconfigured LLM credentials and triggers `_deterministic_heuristic_rca`.
  - Generates structured `Hypothesis` records mapped to empirical metrics.
  - Report conforms to standard `FinalRCAReport` schema with confidence calibrated to **MEDIUM** (heuristic score: 0.65).
- **Validation**: Full UI compatibility maintained with zero user-facing breakage.

---

## 4. Key Engineering Takeaways

1. **Reducer-Safe Concurrent LangGraph Execution**: Using `Annotated[dict[str, str], merge_dict]` prevents `InvalidConcurrentGraphUpdate` errors when parallel agents complete concurrently.
2. **Read-Only Non-Destructive Boundary**: Enforced through strict API boundaries, HTTP GET-only telemetry queries, and recommendation sanitization that scrubs operational commands.
3. **Honest Citation & Validation**: The Evidence Validator verifies citation existence, service attribution, time-window bounding, numerical consistency, and contradictory evidence. It does not pretend to offer an unprovable mathematical zero-hallucination guarantee.
4. **Heuristic Confidence Rating**: Confidence scores (`HIGH`: 0.85, `MEDIUM`: 0.65, `LOW`: 0.30) are deterministic heuristic ratings based on telemetry source diversity and absence of contradictions, not statistical Bayesian probabilities.
