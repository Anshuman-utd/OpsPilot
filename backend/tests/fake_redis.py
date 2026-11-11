"""Deterministic Async Fake Redis implementation for testing distributed locking and concurrency.

Supports:
- SET with NX, PX (milliseconds), and EX (seconds)
- GET and DELETE
- Lua script evaluation for atomic release (DEL if token match)
- Lua script evaluation for atomic renewal (PEXPIRE if token match)
- Lua script evaluation for ownership validation (return 1 if token match)
- Time-based TTL expiration
- Fault injection (simulated network errors and timeouts)
"""

import time
from typing import Any

from redis.exceptions import ConnectionError as RedisConnectionError


class FakeRedis:
    """Async Redis fake for deterministic isolated testing."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expires: dict[str, float] = {}  # key -> monotonic expire timestamp
        self.should_fail: bool = False
        self.failure_exception: Exception | None = None
        self._manual_time: float | None = None

    def _now(self) -> float:
        if self._manual_time is not None:
            return self._manual_time
        return time.time()

    def advance_time(self, seconds: float) -> None:
        """Advance simulated clock by given number of seconds."""
        if self._manual_time is None:
            self._manual_time = time.time()
        self._manual_time += seconds

    def _purge_expired(self) -> None:
        now = self._now()
        expired_keys = [k for k, exp in self.expires.items() if exp <= now]
        for k in expired_keys:
            self.store.pop(k, None)
            self.expires.pop(k, None)

    async def get(self, key: str) -> str | None:
        if self.should_fail:
            raise self.failure_exception or RedisConnectionError("Simulated Redis connection error")
        self._purge_expired()
        return self.store.get(key)

    async def set(
        self,
        key: str,
        value: str,
        nx: bool = False,
        px: int | None = None,
        ex: int | None = None,
    ) -> bool:
        if self.should_fail:
            raise self.failure_exception or RedisConnectionError("Simulated Redis connection error")
        self._purge_expired()

        if nx and key in self.store:
            return False

        self.store[key] = str(value)
        now = self._now()
        if px is not None:
            self.expires[key] = now + (px / 1000.0)
        elif ex is not None:
            self.expires[key] = now + float(ex)
        else:
            self.expires.pop(key, None)
        return True

    async def delete(self, key: str) -> int:
        if self.should_fail:
            raise self.failure_exception or RedisConnectionError("Simulated Redis connection error")
        self._purge_expired()
        if key in self.store:
            del self.store[key]
            self.expires.pop(key, None)
            return 1
        return 0

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        if self.should_fail:
            raise self.failure_exception or RedisConnectionError("Simulated Redis connection error")
        self._purge_expired()

        keys = list(keys_and_args[:numkeys])
        args = list(keys_and_args[numkeys:])

        # 1. Release script: if redis.call("GET", KEYS[1]) == ARGV[1] then return redis.call("DEL", KEYS[1]) else return 0 end
        if "DEL" in script:
            key = keys[0]
            token = str(args[0])
            if self.store.get(key) == token:
                del self.store[key]
                self.expires.pop(key, None)
                return 1
            return 0

        # 2. Renewal script: if redis.call("GET", KEYS[1]) == ARGV[1] then return redis.call("PEXPIRE", KEYS[1], ARGV[2]) else return 0 end
        if "PEXPIRE" in script:
            key = keys[0]
            token = str(args[0])
            px_ms = int(args[1])
            if self.store.get(key) == token:
                self.expires[key] = self._now() + (px_ms / 1000.0)
                return 1
            return 0

        # 3. Validation script: if redis.call("GET", KEYS[1]) == ARGV[1] then return 1 else return 0 end
        if "GET" in script:
            key = keys[0]
            token = str(args[0])
            if self.store.get(key) == token:
                return 1
            return 0

        raise NotImplementedError(f"Unsupported Lua script in FakeRedis: {script}")
