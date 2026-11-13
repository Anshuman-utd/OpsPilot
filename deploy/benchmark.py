"""Measure one lab scenario on a fresh, isolated Compose project."""

import argparse
import asyncio
import json
import secrets
import time
from datetime import datetime

import httpx
from app.accounts import save_account
from app.core.config import get_settings
from app.services.demo import SCENARIOS

CAUSE_TERMS = {
    "payment_failure_spike": ("payment", "decline"),
    "downstream_latency_spike": ("latency", "slow"),
    "db_pool_exhaustion": ("pool", "database"),
    "order_service_error_spike": ("error", "exception"),
    "payment_service_unavailability": ("unavailable", "503"),
    "cascading_timeout": ("timeout", "latency"),
}


def timestamp(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def benchmark(scenario_id: str) -> dict:
    settings = get_settings()
    if not settings.demo_lab_enabled or settings.secure_session_cookie:
        raise RuntimeError("Use the disposable local Compose release with the demo lab enabled.")
    scenario = SCENARIOS[scenario_id]
    username = "bench-" + secrets.token_hex(5)
    password = secrets.token_urlsafe(24)
    asyncio.run(save_account(username, "operator", password))
    result = {"scenario": scenario_id, "service": scenario.service, "detected": False}

    with httpx.Client(base_url="http://edge", headers={
        "Host": httpx.URL(settings.PUBLIC_ORIGIN).netloc.decode(), "Origin": settings.PUBLIC_ORIGIN,
    }, timeout=30) as client:
        client.post("/api/auth/login", json={"username": username, "password": password}).raise_for_status()
        for endpoint in ("http://loki:3100/ready", "http://tempo:3200/ready", "http://prometheus:9090/-/ready"):
            deadline = time.monotonic() + 60
            while client.get(endpoint).status_code != 200:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"Telemetry readiness timed out: {endpoint}")
                time.sleep(2)
        baseline = client.get("/api/v1/incidents", params={"status": "active", "limit": 200}).raise_for_status().json()
        result["baseline_active_incidents"] = len(baseline)
        baseline_ids = {item["id"] for item in baseline}
        job = client.post(f"/api/v1/demo/{scenario_id}").raise_for_status().json()["job"]
        deadline = time.monotonic() + 105
        incident = None
        while time.monotonic() < deadline:
            run = client.get("/api/v1/demo").raise_for_status().json()["run"]
            if run["job"] != job or run["status"] in {"failed", "interrupted"}:
                raise RuntimeError("Demo job stopped before detection.")
            incidents = client.get("/api/v1/incidents", params={
                "status": "active", "service": scenario.service, "limit": 200,
            }).raise_for_status().json()
            start = run.get("fault_started_at")
            if start is not None:
                incident = next((
                    item for item in incidents
                    if item["id"] not in baseline_ids and item["incident_type"] in scenario.signals
                    and timestamp(item["triggered_at"]) >= start
                ), None)
            if incident:
                break
            time.sleep(2)
        if not incident:
            return result

        result.update(
            detected=True,
            signal=incident["incident_type"],
            detection_seconds=round(timestamp(incident["triggered_at"]) - start, 2),
        )
        path = f"/api/v1/incidents/{incident['id']}"
        run = client.post(f"{path}/investigations").raise_for_status().json()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            detail = client.get(f"/api/v1/investigations/{run['id']}").raise_for_status().json()
            if detail["status"] not in {"PENDING", "RUNNING"}:
                break
            time.sleep(2)
        result["investigation"] = detail["status"]
        report = detail.get("final_report") or {}
        evidence = report.get("evidence_sources", [])
        hypotheses = report.get("hypotheses", [])
        source_ids = {item["evidence_id"] for item in evidence}
        citations = {item for hypothesis in hypotheses for item in hypothesis.get("supporting_evidence_ids", [])}
        primary = hypotheses[0] if hypotheses else {}
        primary_text = f"{primary.get('title', '')} {primary.get('explanation', '')}".lower()
        result.update(
            investigation_seconds=round(detail["total_duration_ms"] / 1000, 2) if detail.get("total_duration_ms") else None,
            rca_keyword_match=any(term in primary_text for term in CAUSE_TERMS[scenario_id]),
            evidence_sources=sorted({item["source_type"] for item in evidence}),
            cited_ids=len(citations),
            unresolved_citations=len(citations - source_ids),
            confidence=report.get("confidence_level"),
            model_configured=bool(settings.GROQ_API_KEY),
            synthesis_mode=report.get("synthesis_mode"),
        )
        if detail["status"] != "COMPLETED":
            return result

        plan = client.post(f"{path}/remediation-plans", json={"investigation_id": run["id"]}).raise_for_status().json()
        action = next((
            item for item in plan["actions"]
            if item["status"] == "PENDING_APPROVAL" and item["parameters"].get("fault_type") == scenario_id
        ), None)
        if action is None:
            result["remediation"] = "no_matching_action"
            return result
        plan_path = f"/api/v1/remediation-plans/{plan['id']}"
        client.post(f"{plan_path}/actions/{action['id']}/approve", json={
            "plan_version": plan["version"], "action_content_hash": action["content_hash"],
        }).raise_for_status()
        deadline = time.monotonic() + 210
        status = "PENDING"
        while time.monotonic() < deadline:
            executions = client.get(f"{plan_path}/executions").raise_for_status().json()
            status = executions[0]["status"]
            if status not in {"PENDING", "EXECUTING", "VERIFYING"}:
                break
            time.sleep(3)
        result["remediation"] = status
        if status == "RECOVERED":
            resolved = client.get(path).raise_for_status().json()["resolved_at"]
            verified = executions[0]["verification_runs"][-1]["completed_at"]
            result["recovery_seconds"] = round(timestamp(verified) - start, 2)
            result["incident_resolved"] = resolved is not None
            result["report_download"] = client.get(f"{path}/report").raise_for_status().text.startswith("# ")
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=SCENARIOS)
    args = parser.parse_args()
    result = benchmark(args.scenario)
    print(json.dumps(result, indent=2), flush=True)
    if result.get("remediation") != "RECOVERED" or not result.get("incident_resolved") or not result.get("report_download"):
        raise SystemExit("Scenario did not complete detection, approved remediation, recovery, and reporting.")
