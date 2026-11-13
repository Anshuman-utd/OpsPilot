"""Run async Celery work and release loop-bound connections before closing the loop."""

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

from app.core.database import close_db_engine
from app.core.redis import close_redis

T = TypeVar("T")


def run_async_task(task: Coroutine[Any, Any, T]) -> T:
    async def run() -> T:
        try:
            return await task
        finally:
            try:
                await close_db_engine()
            finally:
                await close_redis()

    return asyncio.run(run())
