#!/usr/bin/env python3
"""
OpsPilot Phase 2 Pipeline Verification CLI
Exercises the live microservice pipeline, verifies telemetry endpoints,
and checks observability stacks (Prometheus, Grafana, Loki, Tempo).
"""

import asyncio

import httpx

GATEWAY_URL = "http://localhost:8001"
ORDER_URL = "http://localhost:8002"
PAYMENT_URL = "http://localhost:8003"
PROMETHEUS_URL = "http://localhost:9090"
GRAFANA_URL = "http://localhost:3001"
LOKI_URL = "http://localhost:3100"
TEMPO_URL = "http://localhost:3200"
OTEL_COLLECTOR_URL = "http://localhost:13133"


async def check_url(client: httpx.AsyncClient, name: str, url: str) -> bool:
    try:
        resp = await client.get(url, timeout=3.0)
        is_ok = resp.status_code in (200, 204)
        marker = "✓" if is_ok else "✗"
        status_str = f"ONLINE ({resp.status_code})" if is_ok else f"STATUS {resp.status_code}"
        print(f"  [{marker}] {name:<22}: {status_str} @ {url}")
        return is_ok
    except Exception as exc:
        print(f"  [✗] {name:<22}: OFFLINE ({exc.__class__.__name__}) @ {url}")
        return False


async def main() -> int:
    print("==================================================================")
    print(" OpsPilot Phase 2 Observability & Microservices Verification")
    print("==================================================================")

    async with httpx.AsyncClient() as client:
        endpoints = {
            "API Gateway": f"{GATEWAY_URL}/health",
            "Order Service": f"{ORDER_URL}/health",
            "Payment Service": f"{PAYMENT_URL}/health",
            "Prometheus": f"{PROMETHEUS_URL}/-/healthy",
            "Grafana": f"{GRAFANA_URL}/api/health",
            "Loki": f"{LOKI_URL}/ready",
            "Tempo": f"{TEMPO_URL}/ready",
            "Otel Collector": f"{OTEL_COLLECTOR_URL}/",
        }
        print("\n1. Verifying service and observability endpoints:")
        endpoint_results = await asyncio.gather(
            *(check_url(client, name, url) for name, url in endpoints.items())
        )
        passed = all(endpoint_results)

        print("\n2. Testing End-to-End Order Creation Flow:")
        try:
            order_payload = {
                "customer_id": "cust-verify-cli",
                "amount": 149.50,
                "currency": "USD",
            }
            res = await client.post(f"{GATEWAY_URL}/api/orders", json=order_payload, timeout=6.0)
            res.raise_for_status()
            data = res.json()
            if (
                res.status_code == 201
                and data.get("status") == "CONFIRMED"
                and data.get("payment_id")
            ):
                print("  [✓] Order Successfully Created & Confirmed:")
                print(f"      - Order ID   : {data.get('id')}")
                print(f"      - Customer ID: {data.get('customer_id')}")
                print(f"      - Status     : {data.get('status')}")
                print(f"      - Payment ID : {data.get('payment_id')}")
            else:
                passed = False
                print(f"  [✗] Payment service did not confirm the order (HTTP {res.status_code})")
        except Exception as exc:
            passed = False
            print(f"  [✗] Order check failed: {exc}")

        print("\n3. Verifying Prometheus scrape results:")
        try:
            response = await client.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": 'up{service=~"api-gateway|order-service|payment-service"}'},
                timeout=3.0,
            )
            response.raise_for_status()
            result = response.json()["data"]["result"]
            healthy = {
                sample["metric"]["service"] for sample in result if sample["value"][1] == "1"
            }
            missing = {"api-gateway", "order-service", "payment-service"} - healthy
            if missing:
                passed = False
                print(f"  [✗] Missing healthy scrape results for {', '.join(sorted(missing))}")
            else:
                print("  [✓] Prometheus is scraping all three demo services")
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            passed = False
            print(f"  [✗] Prometheus scrape check failed: {exc}")

    print("\n==================================================================")
    print(" Verification passed." if passed else " Verification failed.")
    print("==================================================================")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
