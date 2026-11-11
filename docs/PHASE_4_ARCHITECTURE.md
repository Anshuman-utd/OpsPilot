# OpsPilot — Phase 4 Architecture: Autonomous Agentic Investigation & RCA

## 1. Executive Summary

Phase 4 transforms **OpsPilot** from a deterministic incident detection system (Phase 3) into an **autonomous Site Reliability Engineering (SRE) investigation platform**. 

When a metric breach triggers an incident or an operator requests an investigation, OpsPilot initiates a multi-agent investigation workflow orchestrated by **LangGraph**. A swarm of specialized agents correlates metrics (Prometheus), application logs (Loki), distributed traces (Tempo), and historical incident memory (PostgreSQL) to synthesize evidence-backed root-cause hypotheses with calibrated confidence ratings (`LOW`, `MEDIUM`, `HIGH`).

### Strict Phase Boundaries
- **Strictly READ-ONLY**: Agents observe and analyze telemetry. No automated remediation, shell command execution, container restarts, or infrastructure mutations are performed (remediation is reserved for Phase 5 with human-in-the-loop approval). Recommendations are automatically sanitized to prevent any operational commands.
- **Distinction of Fact from Inference**: Observed empirical facts (e.g., recorded error rates, log excerpts, trace span durations) are strictly separated from inferred causal hypotheses.
- **Deterministic Citation & Evidence Validation**: An Evidence Validator agent verifies every cited evidence identifier (`EV-...`) against retrieved telemetry, checks service attribution, bounds timestamps to the incident window, detects contradicting evidence, and calibrates heuristic confidence. Unsupported claims are pruned or marked as unverified assumptions.
- **Single-Pass Bounded DAG**: Investigation executes as a deterministic, single-pass pipeline without unbounded agent loops to guarantee bounded latency and predictable execution costs.
- **Provider Agnostic**: Operates with Groq (`llama-3.3-70b-versatile`) or any OpenAI-compatible provider, with a deterministic heuristic fallback if LLM credentials are not configured. Untrusted telemetry is serialized inside structured JSON boundaries to prevent prompt-injection exploits.

---

## 2. Multi-Agent Orchestration Architecture (LangGraph)

OpsPilot models incident investigation as a directed acyclic state graph (DAG) using **LangGraph**:

```mermaid
flowchart TD
    START([START]) --> Analyzer[Incident Analyzer]
    
    subgraph Parallel Telemetry Swarm [Parallel Telemetry Fan-out]
        Analyzer --> MetricsAgent[Metrics Investigator\n(Prometheus)]
        Analyzer --> LogAgent[Log Investigator\n(Loki)]
        Analyzer --> TraceAgent[Trace Investigator\n(Tempo)]
        Analyzer --> HistAgent[Historical Context\n(PostgreSQL)]
    end
    
    MetricsAgent --> Aggregator[Evidence Aggregator\n(Facts & References)]
    LogAgent --> Aggregator
    TraceAgent --> Aggregator
    HistAgent --> Aggregator
    
    Aggregator --> RCASynthesizer[RCA Synthesizer\n(Hypothesis Generator)]
    RCASynthesizer --> Validator[Evidence Validator\n(Fact-Checker & Calibrator)]
    Validator --> ENDNode([END])

    classDef agent fill:#1e1b4b,stroke:#6366f1,stroke-width:2px,color:#fff;
    classDef sync fill:#0f172a,stroke:#38bdf8,stroke-width:2px,color:#fff;
    class Analyzer,MetricsAgent,LogAgent,TraceAgent,HistAgent,RCASynthesizer,Validator agent;
    class Aggregator sync;
```

### 2.1 Graph State Machine (`InvestigationState`)

The shared workflow state is strongly typed as a `TypedDict` with reducer functions ensuring safe concurrent updates during the parallel fan-out:

```python
class InvestigationState(TypedDict, total=False):
    investigation_id: str
    incident_id: str
    incident_context: dict[str, Any]
    investigation_started_at: str
    investigation_status: str
    affected_service: str
    incident_severity: str
    incident_evidence: dict[str, Any]
    
    # Step 1: Planning
    investigation_plan: dict[str, Any]
    
    # Step 2: Parallel Telemetry Findings
    metrics_findings: dict[str, Any]
    logs_findings: dict[str, Any]
    traces_findings: dict[str, Any]
    historical_findings: dict[str, Any]
    
    # Step 3: Aggregated Evidence & Facts
    observed_facts: list[str]
    evidence_references: list[dict[str, Any]]
    
    # Step 4: RCA Synthesis & Validation
    hypotheses: list[dict[str, Any]]
    synthesis_result: dict[str, Any]
    validation_result: dict[str, Any]
    final_report: dict[str, Any]
    
    # Execution Telemetry (Merged via Reducers)
    agent_statuses: Annotated[dict[str, str], merge_dict]
    agent_errors: Annotated[dict[str, str], merge_dict]
    investigation_completed_at: str
```

---

## 3. Specialized Agents

| Agent Name | Primary Responsibility | Telemetry Source | Output Contract |
| :--- | :--- | :--- | :--- |
| **Incident Analyzer** | Evaluates incident context, determines optimal time window ($t - 15m$ to $t + 5m$), and formulates investigation targets. | Incident metadata & trigger payload | `investigation_plan` |
| **Metrics Investigator** | Queries the 4 Golden Signals: throughput, latency, HTTP 5xx error rate, and DB pool saturation. Identifies trend directions. | Prometheus `/api/v1/query_range` | `metrics_findings` |
| **Log Investigator** | Searches error/exception logs, correlates stack traces, and extracts distributed trace IDs (`trace_id`). | Loki `/loki/api/v1/query_range` | `logs_findings` |
| **Trace Investigator** | Follows extracted trace IDs or searches error traces, identifies root span, bottleneck span, and downstream service failures. | Tempo `/api/traces/{id}` | `traces_findings` |
| **Historical Context** | Queries previous incidents for the service and checks for identical fingerprint recurrence and past RCA notes. | PostgreSQL `incidents` & `investigations` | `historical_findings` |
| **Evidence Aggregator** | Merges all telemetry findings into verifiable `observed_facts` and structured `EvidenceReference` objects (`EV-...`). | In-memory findings | `observed_facts`, `evidence_references` |
| **RCA Synthesizer** | Formulates causal hypotheses backed by evidence references, distinguishing empirical facts from hypotheses. | LLM / Heuristic Engine | `synthesis_result`, `hypotheses` |
| **Evidence Validator** | Verifies cited evidence IDs, prunes uncorroborated claims, and calibrates confidence based on multi-source evidence. | Evidence References | `final_report`, `validation_result` |

---

## 4. Evidence Model & Confidence Calibration

Every piece of evidence is tracked as an `EvidenceReference`:

```json
{
  "evidence_id": "EV-METRIC-01",
  "source_type": "METRIC",
  "source_reference": "sum(rate(http_requests_total{service=\"order-service\", status_code=~\"5..\"}[2m]))",
  "service_name": "order-service",
  "observation": "order-service HTTP error rate peaked at 24.50% (trend: increasing)",
  "observed_value": 0.245,
  "relevant_threshold": 0.05,
  "evidence_strength": "SUPPORTING",
  "supporting_data": {
    "deep_link": "http://localhost:9090/graph?g0.expr=..."
  }
}
```

### Confidence Calibration Matrix

The **Evidence Validator** applies deterministic calibration rules before finalizing the RCA report:

- **HIGH Confidence** (Heuristic Score: `0.85`):
  - Directly corroborated by **at least two distinct telemetry source types** (e.g., `METRIC` breach AND `LOG` exception, or `METRIC` latency AND `TRACE` downstream timeout).
  - All supporting evidence correctly attributes to the affected service or its dependencies.
  - No unresolved contradictions or severe numerical discrepancies.
- **MEDIUM Confidence** (Heuristic Score: `0.65`):
  - Supported by a single distinct telemetry source (e.g., metric breach only or error log only).
  - Plausible causal mechanism, but with single-source dependency.
- **LOW Confidence** (Heuristic Score: `0.30`):
  - Zero matching evidence citations, or total service attribution failure.
  - Partial telemetry outage or missing probe points.

#### Penalties and Contradiction Handling:
- **Contradicted Hypotheses**: If valid `contradicting_evidence_ids` are detected, the hypothesis is marked `is_contested = True`, confidence is downgraded by one full tier (e.g. `HIGH` -> `MEDIUM`, `MEDIUM` -> `LOW`), and score is penalized by -0.25 (capped at max 0.50).
- **Service Attribution / Numerical Discrepancy**: If claims cite completely unrelated services or state numbers contradicting recorded telemetry by > 40 percentage points, confidence is downgraded and penalized.
- **Heuristic Disclaimer**: Scores are deterministic rating indices `[0.0 - 1.0]`, not empirical Bayesian posterior probabilities.

---

## 5. Execution Pipeline & Bounded Latency

Phase 4 is intentionally designed as a **deterministic single-pass DAG** (`incident_analyzer` -> parallel telemetry agents -> `aggregator` -> `rca_agent` -> `validator_agent` -> `END`).
- Unbounded autonomous agent loops are explicitly excluded to maintain predictable investigation duration and prevent runaway LLM token consumption.
- If additional investigation is required after operator review, investigations can be re-triggered on demand via the API or frontend retry action.

---

## 5. Fault Resilience & Graceful Degradation

If any telemetry service is unavailable (e.g., Loki or Tempo down):
1. The respective agent catches the exception, records the failure in `agent_errors`, and populates fallback findings.
2. The LangGraph workflow continues uninterrupted.
3. The Evidence Aggregator compiles all remaining telemetry.
4. The Evidence Validator notes the telemetry gap in `investigation_limitations` and downgrades confidence appropriately.
5. If the LLM provider API key is missing or fails, the system switches to **Deterministic Heuristic RCA**, guaranteeing that every investigation always outputs a valid, structured `FinalRCAReport`.

