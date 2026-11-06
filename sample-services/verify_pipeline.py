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
        status_str = f"ONLINE ({resp.status_code})" if is_ok else f"STATUS {resp.status_code}"
        print(f"  [✓] {name:<22}: {status_str} @ {url}")
        return is_ok
    except Exception as exc:
        print(f"  [✗] {name:<22}: OFFLINE ({exc.__class__.__name__}) @ {url}")
        return False


async def main():
    print("==================================================================")
    print(" OpsPilot Phase 2 Observability & Microservices Verification")
    print("==================================================================")

    async with httpx.AsyncClient() as client:
        print("\n1. Verifying Microservice Endpoints:")
        await check_url(client, "API Gateway", f"{GATEWAY_URL}/health")
        await check_url(client, "Order Service", f"{ORDER_URL}/health")
        await check_url(client, "Payment Service", f"{PAYMENT_URL}/health")

        print("\n2. Verifying Observability Infrastructure:")
        await check_url(client, "Prometheus", f"{PROMETHEUS_URL}/-/healthy")
        await check_url(client, "Grafana", f"{GRAFANA_URL}/api/health")
        await check_url(client, "Loki", f"{LOKI_URL}/ready")
        await check_url(client, "Tempo", f"{TEMPO_URL}/ready")
        await check_url(client, "Otel Collector", f"{OTEL_COLLECTOR_URL}/")

        print("\n3. Testing End-to-End Order Creation Flow:")
        try:
            order_payload = {
                "customer_id": "cust-verify-cli",
                "amount": 149.50,
                "currency": "USD",
            }
            res = await client.post(f"{GATEWAY_URL}/api/orders", json=order_payload, timeout=6.0)
            if res.status_code == 201:
                data = res.json()
                print("  [✓] Order Successfully Created & Confirmed:")
                print(f"      - Order ID   : {data.get('id')}")
                print(f"      - Customer ID: {data.get('customer_id')}")
                print(f"      - Status     : {data.get('status')}")
                print(f"      - Payment ID : {data.get('payment_id')}")
            else:
                print(f"  [✗] Order Creation Failed with status {res.status_code}: {res.text}")
        except Exception as exc:
            print(f"  [✗] Order Creation Network Error: {exc}")

        print("\n4. Verifying Metrics Scraping:")
        try:
            metrics_res = await client.get(f"{GATEWAY_URL}/metrics", timeout=3.0)
            if "http_requests_total" in metrics_res.text:
                print("  [✓] Prometheus Metrics active on API Gateway")
            else:
                print("  [?] Prometheus metrics endpoint returned unexpected payload")
        except Exception as exc:
            print(f"  [✗] Could not scrape metrics: {exc}")

    print("\n==================================================================")
    print(" Verification complete.")
    print("==================================================================")


if __name__ == "__main__":
    asyncio.run(main())
