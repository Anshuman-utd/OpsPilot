"""Redis-backed, ownership-aware distributed lock for remediation execution.

Provides:
- Atomic lock acquisition via SET NX PX
- Service-scoped keys validated against service allowlist
- Unique cryptographically random UUID ownership tokens per attempt
- Atomic compare-and-delete lock release via Lua
- Atomic compare-and-expire lease renewal via Lua
- Background auto-renewal task with safe, leak-free cancellation
- Immediate ownership validation before side-effect dispatch
- Fail-closed behavior on Redis infrastructure failures (no in-memory fallback)
"""

import asyncio
import contextlib
import logging
import uuid
from typing import Any

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.redis import get_redis_client
from app.remediation.registry import ALLOWLISTED_SERVICES

logger = logging.getLogger("opspilot.remediation.distributed_lock")

# Lua Scripts
# Atomic release: only the owner with matching token can delete the lock
RELEASE_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""

# Atomic renewal: only the owner with matching token can extend the lease
RENEW_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("PEXPIRE", KEYS[1], ARGV[2])
else
    return 0
end
"""

# Atomic ownership validation: check whether lock is still held by owner
VALIDATE_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return 1
else
    return 0
end
"""


class DistributedLockError(Exception):
    """Base exception for distributed lock errors."""


class LockContentionError(DistributedLockError):
    """Raised when lock cannot be acquired because another worker holds it."""


class RedisUnavailableError(DistributedLockError):
    """Raised when Redis is unreachable or returns infrastructure errors (fail-closed)."""


class LostLockError(DistributedLockError):
    """Raised when lock ownership is lost during execution before or during protected side effects."""


class RemediationDistributedLock:
    """
    Production-grade distributed lock providing:
    - Atomic acquisition via SET NX PX
    - Service-scoped keys validated against allowlist
    - Unique cryptographic UUID ownership tokens
    - Atomic compare-and-delete release via Lua
    - Atomic compare-and-expire renewal via Lua
    - Background auto-renewal task with safe cancellation
    - Immediate ownership validation before side-effect dispatch
    - Fail-closed behavior on Redis infrastructure failures
    """

    def __init__(
        self,
        service_name: str,
        ttl_seconds: float = 30.0,
        renewal_interval_seconds: float | None = None,
        redis_client: Any | None = None,
    ) -> None:
        if service_name not in ALLOWLISTED_SERVICES:
            raise ValueError(
                f"Invalid or unallowlisted service name '{service_name}'. "
                f"Allowed services: {sorted(ALLOWLISTED_SERVICES)}"
            )

        self.service_name = service_name
        self.lock_key = f"lock:remediation:{service_name}"
        self.ttl_seconds = float(ttl_seconds)
        # Default renewal interval: 1/3 of TTL (bounded minimum 0.5s)
        self.renewal_interval_seconds = (
            float(renewal_interval_seconds)
            if renewal_interval_seconds is not None
            else max(0.5, self.ttl_seconds / 3.0)
        )
        self._custom_client = redis_client

        # State tracking
        self.ownership_token: str | None = None
        self._is_owned: bool = False
        self._lost_ownership: bool = False
        self._renewal_task: asyncio.Task | None = None
        self._stop_renewal_event: asyncio.Event | None = None

    @property
    def client(self) -> Any:
        """Resolve Redis client: custom injected client or singleton client."""
        if self._custom_client is not None:
            return self._custom_client
        return get_redis_client()

    @property
    def is_owned(self) -> bool:
        """Check whether local state believes the lock is held and not lost."""
        return self._is_owned and not self._lost_ownership

    async def acquire(self) -> bool:
        """
        Atomically acquire the distributed lock.
        Generates a fresh unique UUID token for every acquisition attempt.

        Returns:
            True if lock was acquired.
            False if lock is held by another worker (contention).

        Raises:
            RedisUnavailableError: If Redis connection/command fails (fail-closed).
        """
        token = str(uuid.uuid4())
        self.ownership_token = token
        self._is_owned = False
        self._lost_ownership = False

        px_ms = int(self.ttl_seconds * 1000)

        try:
            acquired = await self.client.set(
                self.lock_key,
                token,
                nx=True,
                px=px_ms,
            )
            # redis-py returns True (or "OK") on success, None or False on contention
            if acquired:
                self._is_owned = True
                self._start_renewal()
                logger.info(
                    "Acquired distributed lock for '%s' (token: %s, TTL: %.1fs)",
                    self.service_name,
                    token,
                    self.ttl_seconds,
                )
                return True
            else:
                logger.warning(
                    "Lock contention on '%s': another worker holds lock key '%s'",
                    self.service_name,
                    self.lock_key,
                )
                return False

        except (RedisConnectionError, RedisTimeoutError, RedisError, OSError, ConnectionRefusedError) as exc:
            logger.error(
                "Redis infrastructure failure during lock acquisition on '%s': %s",
                self.service_name,
                exc,
            )
            raise RedisUnavailableError(
                f"Redis unavailable while acquiring lock for '{self.service_name}': {exc}"
            ) from exc

    async def renew(self) -> bool:
        """
        Atomically renew lease for the lock if still owned.

        Returns:
            True if lease was renewed.
            False if lock expired or belongs to another owner.

        Raises:
            RedisUnavailableError: If Redis connection/command fails.
        """
        if not self._is_owned or not self.ownership_token:
            return False

        px_ms = int(self.ttl_seconds * 1000)
        try:
            res = await self.client.eval(
                RENEW_LUA,
                1,
                self.lock_key,
                self.ownership_token,
                px_ms,
            )
            if res == 1:
                logger.debug("Renewed lock lease for '%s' (token: %s)", self.service_name, self.ownership_token)
                return True
            else:
                logger.warning(
                    "Failed to renew lock lease for '%s': token mismatch or expired. Ownership lost.",
                    self.service_name,
                )
                self._is_owned = False
                self._lost_ownership = True
                return False

        except (RedisConnectionError, RedisTimeoutError, RedisError, OSError, ConnectionRefusedError) as exc:
            logger.error("Redis infrastructure failure during renewal on '%s': %s", self.service_name, exc)
            self._lost_ownership = True
            raise RedisUnavailableError(
                f"Redis unavailable while renewing lock for '{self.service_name}': {exc}"
            ) from exc

    async def release(self) -> bool:
        """
        Atomically release the lock using Lua compare-and-delete.
        Safe against deleting successor locks if this lock has expired.
        Safe to call multiple times (idempotent).

        Returns:
            True if deleted lock owned by this instance.
            False if lock was already expired, not owned, or owned by another worker.

        Raises:
            RedisUnavailableError: If Redis connection/command fails.
        """
        await self._stop_renewal()

        if not self.ownership_token or not self._is_owned:
            self._is_owned = False
            return False

        try:
            res = await self.client.eval(
                RELEASE_LUA,
                1,
                self.lock_key,
                self.ownership_token,
            )
            success = (res == 1)
            if success:
                logger.info("Released lock for '%s' (token: %s)", self.service_name, self.ownership_token)
            else:
                logger.warning(
                    "Lock release for '%s' returned 0 (already expired or owned by another worker)",
                    self.service_name,
                )
            return success

        except (RedisConnectionError, RedisTimeoutError, RedisError, OSError, ConnectionRefusedError) as exc:
            logger.error("Redis infrastructure failure during release on '%s': %s", self.service_name, exc)
            raise RedisUnavailableError(
                f"Redis unavailable while releasing lock for '{self.service_name}': {exc}"
            ) from exc
        finally:
            self._is_owned = False

    async def validate_ownership(self) -> bool:
        """
        Verify that this instance still owns the lock in Redis.
        Called immediately before executing outbound side effects.

        Returns:
            True if lock is valid and owned by this instance.
            False if lock expired, lost, or token mismatch.
        """
        if not self.is_owned or not self.ownership_token:
            return False

        try:
            res = await self.client.eval(
                VALIDATE_LUA,
                1,
                self.lock_key,
                self.ownership_token,
            )
            if res == 1:
                return True
            self._is_owned = False
            self._lost_ownership = True
            return False
        except Exception as exc:
            logger.error("Failed to validate lock ownership on '%s': %s", self.service_name, exc)
            self._is_owned = False
            self._lost_ownership = True
            return False

    def _start_renewal(self) -> None:
        """Spawn background task for periodic lease renewal."""
        if self.renewal_interval_seconds <= 0:
            return
        self._stop_renewal_event = asyncio.Event()
        self._renewal_task = asyncio.create_task(self._auto_renewal_loop())

    async def _auto_renewal_loop(self) -> None:
        """Periodically renew lease until explicitly stopped or ownership is lost."""
        try:
            while self._stop_renewal_event and not self._stop_renewal_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop_renewal_event.wait(),
                        timeout=self.renewal_interval_seconds,
                    )
                    # Stop event was signaled
                    break
                except TimeoutError:
                    pass

                if not self._is_owned or (self._stop_renewal_event and self._stop_renewal_event.is_set()):
                    break

                try:
                    renewed = await self.renew()
                    if not renewed:
                        break
                except Exception:
                    # Redis error during background renewal: mark lost and exit loop
                    break
        except asyncio.CancelledError:
            pass

    async def _stop_renewal(self) -> None:
        """Stop background renewal task cleanly without hanging."""
        if self._stop_renewal_event:
            self._stop_renewal_event.set()
        if self._renewal_task and not self._renewal_task.done():
            self._renewal_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._renewal_task
        self._renewal_task = None

    async def __aenter__(self) -> "RemediationDistributedLock":
        acquired = await self.acquire()
        if not acquired:
            raise LockContentionError(
                f"Could not acquire lock for service '{self.service_name}': lock held by another worker"
            )
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            await self.release()
        except Exception as exc:
            logger.warning("Error releasing lock in __aexit__: %s", exc)
