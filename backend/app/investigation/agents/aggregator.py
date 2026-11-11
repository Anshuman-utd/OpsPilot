"""Evidence Aggregator for deterministically synthesizing empirical facts and references."""

import logging
from typing import Any

from app.investigation.state import (
    EvidenceReference,
    EvidenceSourceType,
    EvidenceStrength,
    InvestigationState,
)

logger = logging.getLogger("opspilot.investigation.agents.aggregator")


async def run_evidence_aggregator(state: InvestigationState) -> dict[str, Any]:
    """
    Merge telemetry findings from metrics, logs, traces, and historical investigations
    into a structured list of verified evidence references and empirical observed facts.
    """
    service = state.get("affected_service", "order-service")
    mf = state.get("metrics_findings", {})
    lf = state.get("logs_findings", {})
    tf = state.get("traces_findings", {})
    hf = state.get("historical_findings", {})

    observed_facts: list[str] = []
    evidence_refs: list[dict[str, Any]] = []

    # 1. Process Metrics
    if mf and not mf.get("error"):
        # Error rate
        err_data = mf.get("error_rate", {})
        if err_data and err_data.get("success"):
            for s in err_data.get("series", []):
                latest = s.get("latest_value", 0.0)
                peak = s.get("max_value", 0.0)
                trend = err_data.get("overall_trend", "stable")
                fact = f"{service} HTTP error rate peaked at {peak:.2f} (latest: {latest:.2f}, trend: {trend})."
                observed_facts.append(fact)

                ev = EvidenceReference(
                    evidence_id=f"EV-METRIC-0{len(evidence_refs) + 1}",
                    source_type=EvidenceSourceType.METRIC,
                    source_reference=err_data.get("query", ""),
                    service_name=service,
                    observation=fact,
                    observed_value=peak,
                    relevant_threshold=0.05,
                    relevance=f"Indicates elevated failure rate on {service}",
                    evidence_strength=EvidenceStrength.SUPPORTING
                    if peak > 0.05
                    else EvidenceStrength.INCONCLUSIVE,
                    supporting_data={
                        "trend": trend,
                        "series": s,
                        "deep_link": err_data.get("deep_link", ""),
                    },
                )
                evidence_refs.append(ev.model_dump())
                break

        # P95 Latency
        lat_data = mf.get("p95_latency", {})
        if lat_data and lat_data.get("success"):
            for s in lat_data.get("series", []):
                peak_lat = s.get("max_value", 0.0)
                if peak_lat > 0.0:
                    fact = f"{service} P95 response latency peaked at {peak_lat:.3f}s."
                    observed_facts.append(fact)

                    ev = EvidenceReference(
                        evidence_id=f"EV-METRIC-0{len(evidence_refs) + 1}",
                        source_type=EvidenceSourceType.METRIC,
                        source_reference=lat_data.get("query", ""),
                        service_name=service,
                        observation=fact,
                        observed_value=peak_lat,
                        relevant_threshold=1.0,
                        relevance=f"Response latency breach on {service}",
                        evidence_strength=EvidenceStrength.SUPPORTING
                        if peak_lat > 1.0
                        else EvidenceStrength.INCONCLUSIVE,
                        supporting_data={"series": s, "deep_link": lat_data.get("deep_link", "")},
                    )
                    evidence_refs.append(ev.model_dump())
                    break

        # DB Pool Saturation
        db_data = mf.get("db_pool_saturation", {})
        if db_data and db_data.get("success"):
            for s in db_data.get("series", []):
                peak_db = s.get("max_value", 0.0)
                if peak_db > 50.0:
                    fact = (
                        f"Database connection pool saturation for {service} reached {peak_db:.1f}%."
                    )
                    observed_facts.append(fact)

                    ev = EvidenceReference(
                        evidence_id=f"EV-METRIC-0{len(evidence_refs) + 1}",
                        source_type=EvidenceSourceType.METRIC,
                        source_reference=db_data.get("query", ""),
                        service_name=service,
                        observation=fact,
                        observed_value=peak_db,
                        relevant_threshold=75.0,
                        relevance=f"High DB pool utilization may cause query queuing or connection starvation on {service}",
                        evidence_strength=EvidenceStrength.CORROBORATING
                        if peak_db > 75.0
                        else EvidenceStrength.INCONCLUSIVE,
                        supporting_data={"series": s, "deep_link": db_data.get("deep_link", "")},
                    )
                    evidence_refs.append(ev.model_dump())
                    break

        # Payment Failure
        pay_data = mf.get("payment_failure")
        if pay_data and pay_data.get("success"):
            for s in pay_data.get("series", []):
                peak_pay = s.get("max_value", 0.0)
                if peak_pay > 5.0:
                    fact = f"Payment failure ratio reached {peak_pay:.1f}%."
                    observed_facts.append(fact)

                    ev = EvidenceReference(
                        evidence_id=f"EV-METRIC-0{len(evidence_refs) + 1}",
                        source_type=EvidenceSourceType.METRIC,
                        source_reference=pay_data.get("query", ""),
                        service_name="payment-service",
                        observation=fact,
                        observed_value=peak_pay,
                        relevant_threshold=20.0,
                        relevance="Downstream payment failures impacting order processing",
                        evidence_strength=EvidenceStrength.SUPPORTING
                        if peak_pay > 20.0
                        else EvidenceStrength.INCONCLUSIVE,
                        supporting_data={"series": s, "deep_link": pay_data.get("deep_link", "")},
                    )
                    evidence_refs.append(ev.model_dump())
                    break

        # Downstream Latency
        ds_data = mf.get("downstream_latency")
        if ds_data and ds_data.get("success"):
            for s in ds_data.get("series", []):
                peak_ds = s.get("max_value", 0.0)
                if peak_ds > 0.5:
                    fact = f"Downstream service call latency from {service} peaked at {peak_ds:.3f}s."
                    observed_facts.append(fact)

                    ev = EvidenceReference(
                        evidence_id=f"EV-METRIC-0{len(evidence_refs) + 1}",
                        source_type=EvidenceSourceType.METRIC,
                        source_reference=ds_data.get("query", ""),
                        service_name=service,
                        observation=fact,
                        observed_value=peak_ds,
                        relevant_threshold=0.5,
                        relevance=f"Downstream latency propagation to {service}",
                        evidence_strength=EvidenceStrength.SUPPORTING
                        if peak_ds > 0.5
                        else EvidenceStrength.INCONCLUSIVE,
                        supporting_data={"series": s, "deep_link": ds_data.get("deep_link", "")},
                    )
                    evidence_refs.append(ev.model_dump())
                    break

    # 2. Process Logs
    if lf and not lf.get("error"):
        err_logs_wrapper = lf.get("error_logs", {})
        raw_logs = err_logs_wrapper.get("logs", [])
        total_errs = lf.get("total_error_logs", len(raw_logs))
        trace_ids = lf.get("extracted_trace_ids", [])

        if total_errs > 0:
            fact = f"Loki retrieved {total_errs} error log entries for {service} with {len(trace_ids)} distinct trace ID(s)."
            observed_facts.append(fact)

            # Sample top representative error logs
            top_logs = raw_logs[:3]
            for _idx, log_entry in enumerate(top_logs):
                line_sample = log_entry.get("raw_text", "")
                ev = EvidenceReference(
                    evidence_id=f"EV-LOG-0{len(evidence_refs) + 1}",
                    source_type=EvidenceSourceType.LOG,
                    source_reference=err_logs_wrapper.get("query", f'{{service="{service}"}}'),
                    service_name=service,
                    observation=f"Error log: {line_sample[:180]}",
                    observed_value=log_entry.get("level", "ERROR"),
                    relevance="Direct application error message recorded during incident",
                    evidence_strength=EvidenceStrength.SUPPORTING,
                    supporting_data={
                        "trace_id": log_entry.get("trace_id"),
                        "raw_line": line_sample,
                        "deep_link": err_logs_wrapper.get("deep_link", ""),
                    },
                )
                evidence_refs.append(ev.model_dump())

    # 3. Process Traces
    if tf and not tf.get("error"):
        traces_list = tf.get("traces", [])
        bottleneck = tf.get("primary_bottleneck")
        services_inv = tf.get("services_involved", [])

        if traces_list:
            fact = (
                f"Tempo analyzed {len(traces_list)} trace(s) across services: "
                f"{', '.join(services_inv) or service}."
            )
            if bottleneck:
                fact += f" Primary bottleneck: {bottleneck.get('service')}::{bottleneck.get('operation')} ({bottleneck.get('duration_ms')}ms)."
            observed_facts.append(fact)

            for _idx, tr in enumerate(traces_list[:2]):
                tid = tr.get("trace_id", "")
                failed_spans = tr.get("failed_spans", [])
                if failed_spans:
                    f_span = failed_spans[0]
                    ev = EvidenceReference(
                        evidence_id=f"EV-TRACE-0{len(evidence_refs) + 1}",
                        source_type=EvidenceSourceType.TRACE,
                        source_reference=f"trace_id:{tid} span_id:{f_span.get('span_id')}",
                        service_name=f_span.get("service", service),
                        observation=(
                            f"Trace {tid[:16]}... span {f_span.get('operation')} failed with "
                            f"HTTP {f_span.get('http_status')} ({f_span.get('status_message') or 'error'})."
                        ),
                        observed_value=f_span.get("http_status") or f_span.get("duration_ms"),
                        relevance="Distributed trace span failed causing call cascade failure",
                        evidence_strength=EvidenceStrength.SUPPORTING,
                        supporting_data={"trace": tr, "deep_link": tr.get("deep_link", "")},
                    )
                    evidence_refs.append(ev.model_dump())

    # 4. Process Historical Context
    if hf and not hf.get("error"):
        past_count = hf.get("past_incidents_count", 0)
        rec_count = hf.get("recurrence_count", 0)
        if past_count > 0:
            fact = f"Incident history shows {past_count} past incident(s) for {service} ({rec_count} recurring with identical fingerprint)."
            observed_facts.append(fact)

            prev_rcas = hf.get("previous_rca_summaries", [])
            prev_rca_text = prev_rcas[0] if prev_rcas else "None recorded"

            ev = EvidenceReference(
                evidence_id=f"EV-HIST-0{len(evidence_refs) + 1}",
                source_type=EvidenceSourceType.INCIDENT_HISTORY,
                source_reference=hf.get("fingerprint", "history"),
                service_name=service,
                observation=fact,
                observed_value=rec_count,
                relevance="Identifies whether this failure mode is recurring",
                evidence_strength=EvidenceStrength.CORROBORATING
                if rec_count > 0
                else EvidenceStrength.INCONCLUSIVE,
                supporting_data={"previous_rca_summary": prev_rca_text},
            )
            evidence_refs.append(ev.model_dump())

    if not observed_facts:
        observed_facts.append(
            f"No anomalous telemetry points observed for {service} within window."
        )

    logger.info(
        "Evidence aggregator compiled %d observed facts and %d evidence references",
        len(observed_facts),
        len(evidence_refs),
    )

    return {
        "observed_facts": observed_facts,
        "evidence_references": evidence_refs,
        "agent_statuses": {"aggregator": "COMPLETED"},
    }
