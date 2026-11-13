"""Run from the release API container with demo faults enabled and fresh data."""

import asyncio
import secrets
import time

import httpx
from app.accounts import save_account
from app.core.config import get_settings

PAYMENT = "http://payment-service:8003"
FAULT = f"{PAYMENT}/debug/faults/payment_failure_spike"


def wait_for(get_result, predicate, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = get_result()
        if predicate(result):
            return result
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {get_result.__name__}: {result}")


def main():
    settings = get_settings()
    assert settings.demo_lab_enabled and not settings.secure_session_cookie
    password = secrets.token_urlsafe(24)
    username = "smoke-" + secrets.token_hex(4)
    asyncio.run(save_account(username, "operator", password))
    origin = settings.PUBLIC_ORIGIN
    with httpx.Client(base_url="http://edge", headers={"Host": httpx.URL(origin).netloc.decode()}, timeout=40) as app:
        assert app.get("/").raise_for_status().status_code == 200
        assert app.get("/api/v1/incidents").status_code == 401
        assert app.get("/health/ready").status_code == 404
        for health_url in ("http://loki:3100/ready", "http://tempo:3200/ready", "http://prometheus:9090/-/ready"):
            wait_for(lambda url=health_url: app.get(url).status_code, lambda code: code == 200, 60)
        app.headers["Origin"] = origin
        app.post("/api/auth/login", json={"username": username, "password": password}).raise_for_status()
        app.get("/api/v1/services/health").raise_for_status()
        invite = app.post("/api/auth/invites").raise_for_status().json()["code"]
        with httpx.Client(base_url="http://edge", headers=app.headers) as viewer:
            viewer.post("/api/auth/signup", json={
                "username": username + "-viewer", "password": password, "invite_code": invite,
            }).raise_for_status()
            assert viewer.get("/api/auth/session").json()["role"] == "viewer"
            assert viewer.post("/api/v1/demo/payment_failure_spike").status_code == 403
        app.post("/api/v1/demo/payment_failure_spike").raise_for_status()
        try:
            def find_payment_incident():
                run = app.get("/api/v1/demo").raise_for_status().json()["run"]
                return run.get("incident") if run else None

            incident = wait_for(find_payment_incident, bool)
            incident_path = f"/api/v1/incidents/{incident['id']}"
            run = app.post(f"{incident_path}/investigations").raise_for_status().json()
            wait_for(
                lambda: app.get(f"/api/v1/investigations/{run['id']}").raise_for_status().json(),
                lambda value: value["status"] == "COMPLETED",
            )
            plan = app.post(f"{incident_path}/remediation-plans", json={"investigation_id": run["id"]}).raise_for_status().json()
            action = next(item for item in plan["actions"] if item["status"] == "PENDING_APPROVAL")
            plan_path = f"/api/v1/remediation-plans/{plan['id']}"
            approval = app.post(f"{plan_path}/actions/{action['id']}/approve", json={
                "plan_version": plan["version"], "approver_id": "spoofed",
                "action_content_hash": action["content_hash"],
            }).raise_for_status().json()
            assert approval["approver_id"] == username

            wait_for(
                lambda: app.get(FAULT.rsplit("/", 1)[0]).raise_for_status().json(),
                lambda faults: not next(f for f in faults if f["name"] == "payment_failure_spike")["enabled"],
            )
            def recovery():
                executions = app.get(f"{plan_path}/executions").raise_for_status().json()
                return executions[0]["status"]

            # The default verifier window overlaps the original fault and needs time to clear.
            assert wait_for(recovery, lambda state: state in {"RECOVERED", "FAILED", "VERIFICATION_FAILED"}, 240) == "RECOVERED"
            assert app.get(incident_path).raise_for_status().json()["status"] == "RESOLVED"
            report = app.get(f"{incident_path}/report").raise_for_status()
            assert "RECOVERED" in report.text and "## Investigation" in report.text
            app.post("/api/auth/logout").raise_for_status()
            assert app.get("/api/auth/session").status_code == 401
            print("Verified signup, sign-in, payment incident detection, RCA, approval, recovery, report, and sign-out.")
        finally:
            app.post(f"{FAULT}/disable").raise_for_status()


if __name__ == "__main__":
    main()
