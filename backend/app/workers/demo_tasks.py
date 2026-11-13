"""Generate synthetic traffic and apply one allowlisted demo fault."""

import asyncio
import json
import logging
import time

import httpx

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.redis import get_redis_client
from app.services.demo import DEMO_LEASE, DEMO_RUNTIME, DEMO_STATUS, SCENARIOS
from app.workers.async_runtime import run_async_task

logger = logging.getLogger(__name__)

RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


async def run_scenario(job: str, scenario_id: str) -> None:
    settings = get_settings()
    redis = get_redis_client()
    if await redis.get(DEMO_LEASE) != job:
        return
    if not await redis.set(f"opspilot:demo:claim:{job}", "1", nx=True, ex=600):
        return
    scenario = SCENARIOS[scenario_id]
    target = {
        "order-service": settings.ORDER_SERVICE_URL,
        "payment-service": settings.PAYMENT_SERVICE_URL,
    }[scenario.service]
    fault = f"{target}/debug/faults/{scenario_id}"
    stage = "failed"
    count = 0
    fault_started_at = None

    async def progress(status: str) -> None:
        if await redis.get(DEMO_LEASE) != job:
            raise RuntimeError("Demo lease expired.")
        await redis.set(DEMO_STATUS, json.dumps({
            "job": job, "scenario": scenario_id, "status": status, "orders_sent": count,
            "fault_started_at": fault_started_at,
        }), ex=600)

    async def order(client: httpx.AsyncClient) -> None:
        nonlocal count
        try:
            await client.post(f"{settings.API_GATEWAY_URL}/api/orders", json={
                "customer_id": f"demo-{job[:8]}", "amount": 12, "currency": "USD",
            })
            count += 1
        except httpx.RequestError:
            logger.info("Synthetic request timed out in scenario %s", scenario_id)

    try:
        if not settings.demo_lab_enabled:
            return
        async with httpx.AsyncClient(timeout=9, trust_env=False) as client:
            await progress("warming")
            for _ in range(3):
                await order(client)
                await asyncio.sleep(2)
            await progress("warming")
            response = await client.post(f"{fault}/enable", json={
                "duration_seconds": 120, "params": {**scenario.params, "run_id": job},
            })
            response.raise_for_status()
            fault_started_at = time.time()
            await progress("running")
            concurrency = 7 if scenario_id == "db_pool_exhaustion" else 3
            deadline = time.monotonic() + DEMO_RUNTIME - 10
            while time.monotonic() < deadline:
                async with asyncio.TaskGroup() as group:
                    for _ in range(concurrency):
                        group.create_task(order(client))
                await progress("running")
                await asyncio.sleep(3)
            stage = "completed"
    except Exception:
        logger.exception("Demo scenario %s failed", scenario_id)
    finally:
        try:
            await progress(stage)
        finally:
            await redis.eval(RELEASE, 1, DEMO_LEASE, job)


@celery_app.task(name="app.workers.demo_tasks.run_demo_scenario", time_limit=250, ignore_result=True)
def run_demo_scenario(job: str, scenario_id: str) -> None:
    run_async_task(run_scenario(job, scenario_id))
