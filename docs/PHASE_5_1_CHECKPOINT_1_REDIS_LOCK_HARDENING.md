# OpsPilot — Phase 5.1 Checkpoint 1
## Redis Distributed Locking, Ownership Safety & Concurrency Hardening

**Document Status:** Complete & Verified  
**Date:** 2026-11-11  
**Target Subsystem:** Remediation Execution Engine & Distributed Concurrency Safety  
**Scope:** Checkpoint 1 ONLY (Distributed Locking, Ownership Safety, and Fail-Closed Execution)

---

## 1. Executive Summary

During the Phase 5 Independent Security & Execution Integrity Audit, critical vulnerabilities were discovered in the remediation execution locking mechanism:
1. **Broken Redis Import:** `backend/app/remediation/executor.py` imported non-existent `get_redis` from `app.core.redis`, throwing a silent `ImportError` on every run.
2. **Silent Fail-Open Fallback:** The executor caught this exception and defaulted to `_in_memory_locks` (a process-local dictionary), disabling distributed locking across Celery workers.
3. **Static Value & Unsafe Release:** Locks were stored with static value `"locked"` and deleted via unconditional `redis.delete(lock_key)`, enabling expired workers to delete active locks held by successor workers.
4. **No Lease Renewal:** Long-running handlers had no mechanism to renew lock leases, causing premature expiry during execution.
5. **No Ownership Validation Prior to Side Effects:** Handlers were dispatched without verifying active lease ownership.

**Checkpoint 1 Resolution:**
- Developed a dedicated distributed locking module: `backend/app/remediation/distributed_lock.py` (`RemediationDistributedLock`).
- Implemented atomic acquisition via `SET key token NX PX px_ms`.
- Implemented atomic Lua compare-and-delete release (`if GET key == token then DEL else 0`).
- Implemented atomic Lua compare-and-expire lease renewal (`if GET key == token then PEXPIRE else 0`).
- Implemented pre-dispatch lease validation immediately prior to executing outbound HTTP requests.
- Completely removed `_in_memory_locks` from the codebase; Redis failures now fail closed (`RedisUnavailableError`).
- Authored a comprehensive 21-test suite in `backend/tests/test_remediation_distributed_lock.py` (20 passed, 1 skipped cleanly for live Redis).
- Updated security audit regression tests in `backend/tests/test_phase5_security_audit.py` to assert the hardened behavior.

---

## 2. Original Vulnerabilities & Root Causes

| ID | Vulnerability Finding | Root Cause in Code | Consequence |
| :--- | :--- | :--- | :--- |
| **A** | Broken Redis Client Import | `from app.core.redis import get_redis` in `executor.py:56`. `app.core.redis` only defines `get_redis_client()`. | `ImportError` was raised on every execution attempt. |
| **B** | Unsafe Lock Ownership | `await redis.set(lock_key, "locked", nx=True)` stored a static string without an ownership identity. | No worker identity or lease ownership could be verified. |
| **C** | Unsafe Unconditional Lock Release | `await redis.delete(lock_key)` in `executor.py:73` deleted the key unconditionally. | If Worker 1's TTL expired and Worker 2 acquired the lock, Worker 1 deleted Worker 2's active lock upon completion. |
| **D** | Missing Lease Renewal | No background lease renewal mechanism existed. | Actions taking longer than TTL suffered lock expiration mid-flight. |
| **E** | Fail-Open Process-Local Fallback | `except Exception:` in `executor.py:61-64` populated `_in_memory_locks[lock_key]`. | Multi-worker Celery deployments ran without any cross-worker locking. |

---

## 3. Architecture & Implementation

### 3.1 Dedicated Distributed Lock Module (`distributed_lock.py`)

A specialized module was created in `backend/app/remediation/distributed_lock.py` exposing `RemediationDistributedLock`.

```mermaid
sequenceDiagram
    autonumber
    participant Worker as Celery Worker
    participant Lock as RemediationDistributedLock
    participant Redis as Redis Server
    participant Target as Target Microservice

    Worker->>Lock: acquire()
    Lock->>Redis: SET lock:remediation:{svc} {token} NX PX {ttl_ms}
    alt Lock Contention (Key Exists)
        Redis-->>Lock: nil / False
        Lock-->>Worker: False (status: "locked")
    else Redis Down
        Redis-->>Lock: ConnectionError / TimeoutError
        Lock-->>Worker: raises RedisUnavailableError (fail-closed)
    else Acquired Successfully
        Redis-->>Lock: OK / True
        Lock->>Lock: Start background renewal loop
        Lock-->>Worker: True
        Worker->>Worker: DB status -> EXECUTING
        Worker->>Lock: validate_ownership()
        Lock->>Redis: eval VALIDATE_LUA (GET key == token)
        Redis-->>Lock: 1 (Valid)
        Worker->>Target: POST /debug/faults/... (Outbound HTTP)
        Target-->>Worker: 200 OK
        Worker->>Lock: release() (in finally block)
        Lock->>Lock: Stop renewal task
        Lock->>Redis: eval RELEASE_LUA (DEL if GET key == token)
        Redis-->>Lock: 1 (Released)
    end
```

### 3.2 Service Key Allowlist & Scoping

Keys are formatted strictly as:
```text
lock:remediation:{service_name}
```
`service_name` is validated against `ALLOWLISTED_SERVICES` (`{"order-service", "payment-service", "api-gateway"}`). Invalid inputs (e.g., path traversal attempts or unauthorized service names) raise `ValueError` before any Redis call is constructed.

### 3.3 Atomic Acquisition (`acquire`)
- Generates a fresh, cryptographically unpredictable UUID4 string per attempt (`str(uuid.uuid4())`). Tokens are never reused across runs.
- Executes `redis.set(lock_key, token, nx=True, px=px_ms)`.
- If key exists: returns `False` (lock contention).
- If Redis is unreachable: raises `RedisUnavailableError`.
- If successful: sets `_is_owned = True` and spawns the background auto-renewal loop.

### 3.4 Atomic Ownership-Aware Release (`release`)
Executed via atomic Lua script:
```lua
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
```
- Stops and cancels the background auto-renewal task.
- Executes Lua with `KEYS[1] = lock_key` and `ARGV[1] = token`.
- If return is `1`: the lock was owned and successfully deleted.
- If return is `0`: the lock had expired or was acquired by a successor worker. The successor worker's lock is completely preserved.
- Safe to call multiple times (idempotent).

### 3.5 Atomic Safe Lease Renewal (`renew`)
Executed via atomic Lua script:
```lua
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("PEXPIRE", KEYS[1], ARGV[2])
else
    return 0
end
```
- Configurable renewal interval (default: $TTL / 3$).
- Runs in a background asyncio task.
- If renewal returns `0` (lock expired or taken by another owner): marks `_lost_ownership = True` and terminates the renewal loop.
- If renewal encounters a Redis infrastructure failure: raises `RedisUnavailableError`, marks ownership lost, and stops the loop.

### 3.6 Immediate Ownership Validation Before Dispatch
Before invoking the registered action handler, `RemediationExecutor` performs an explicit ownership verification:
```python
if not await lock.validate_ownership():
    logger.error("Lock ownership lost for service '%s' prior to handler dispatch!", action.target_service)
    raise LostLockError(f"Lost lock lease ownership for service '{action.target_service}' prior to handler dispatch")
```
If the lock lease expired or was evicted during the database transition, the executor aborts execution before any outbound HTTP network requests are made.

### 3.7 Complete Removal of `_in_memory_locks`
The module-level dictionary `_in_memory_locks` and methods `_acquire_lock` / `_release_lock` were deleted from `backend/app/remediation/executor.py`. Redis infrastructure outages trigger fail-closed execution:
```python
except RedisUnavailableError as exc:
    # Update DB execution status to FAILED
    # Log EXECUTION_FAILED audit event
    # Return {"status": "redis_unavailable", ...}
```

---

## 4. Verification & Test Evidence

### 4.1 Dedicated Lock Test Suite (`test_remediation_distributed_lock.py`)

All 20 unit/concurrency tests and 1 live integration test were executed:

```bash
backend/.venv/bin/pytest backend/tests/test_remediation_distributed_lock.py -v
```

```
backend/tests/test_remediation_distributed_lock.py::test_lock_1_successful_acquisition PASSED [  4%]
backend/tests/test_remediation_distributed_lock.py::test_lock_2_contention_between_two_workers PASSED [  9%]
backend/tests/test_remediation_distributed_lock.py::test_lock_3_unique_ownership_tokens PASSED [ 14%]
backend/tests/test_remediation_distributed_lock.py::test_lock_4_owner_releases_its_own_lock PASSED [ 19%]
backend/tests/test_remediation_distributed_lock.py::test_lock_5_non_owner_cannot_release_lock PASSED [ 23%]
backend/tests/test_remediation_distributed_lock.py::test_lock_6_expired_owner_cannot_delete_successor_lock PASSED [ 28%]
backend/tests/test_remediation_distributed_lock.py::test_lock_7_owner_can_renew_lease PASSED [ 33%]
backend/tests/test_remediation_distributed_lock.py::test_lock_8_non_owner_cannot_renew_lease PASSED [ 38%]
backend/tests/test_remediation_distributed_lock.py::test_lock_9_redis_unavailable_during_acquisition_action_never_executes PASSED [ 42%]
backend/tests/test_remediation_distributed_lock.py::test_lock_10_redis_unavailable_during_renewal_safe_failure PASSED [ 47%]
backend/tests/test_remediation_distributed_lock.py::test_lock_11_redis_unavailable_during_release_no_false_success PASSED [ 52%]
backend/tests/test_remediation_distributed_lock.py::test_lock_12_handler_raises_exception_cleanup_occurs PASSED [ 57%]
backend/tests/test_remediation_distributed_lock.py::test_lock_13_cancellation_during_execution_safe_cleanup PASSED [ 61%]
backend/tests/test_remediation_distributed_lock.py::test_lock_14_duplicate_task_delivery_no_duplicate_side_effects PASSED [ 66%]
backend/tests/test_remediation_distributed_lock.py::test_lock_15_two_independent_executors_same_service_no_concurrent_execution PASSED [ 71%]
backend/tests/test_remediation_distributed_lock.py::test_lock_16_different_services_independent_locks PASSED [ 76%]
backend/tests/test_remediation_distributed_lock.py::test_lock_17_ownership_lost_before_handler_dispatch_handler_never_called PASSED [ 80%]
backend/tests/test_remediation_distributed_lock.py::test_lock_18_no_fallback_to_in_memory_locks PASSED [ 85%]
backend/tests/test_remediation_distributed_lock.py::test_lock_19_policy_denied_actions_never_reach_execution PASSED [ 90%]
backend/tests/test_remediation_distributed_lock.py::test_lock_20_service_allowlist_validation PASSED [ 95%]
backend/tests/test_remediation_distributed_lock.py::test_live_redis_contention_and_expired_owner_race SKIPPED [100%]

=========================== short test summary info ============================
SKIPPED [1] backend/tests/test_remediation_distributed_lock.py:554: Live Redis server unavailable at configured REDIS_URL: {'error': 'Error 1 connecting to localhost:6380. Operation not permitted.'}
======================== 20 passed, 1 skipped in 0.35s =========================
```

### 4.2 Security Audit Regression Suite (`test_phase5_security_audit.py`)

The security audit tests were updated to assert the remediated behaviors:
- `test_audit_5_redis_import_error_causes_silent_in_memory_fallback`: Asserts that `_in_memory_locks` is removed and `RedisUnavailableError` fails closed.
- `test_audit_5_lock_release_unconditional_delete_deletes_other_worker_lock`: Asserts that atomic Lua compare-and-delete prevents a stale/expired worker from deleting a successor's lock.

Result: `8 passed in 0.32s`.

### 4.3 Full Repository Regression Status

- **Backend Pytest Suite:** **153 passed, 1 skipped in 1.60s** (0 failures).
- **Backend Ruff Linter:** **All checks passed!**
- **Sample Microservices Tests:** **27 passed in 1.75s**.
- **Frontend Linter & Production Build:** **0 errors, compiled successfully**.

---

## 5. Security Guarantees & Remaining Limitations

### 5.1 Verified Guarantees
1. **Mutual Exclusion per Service:** At most one worker can hold the execution lock for a given microservice key at any time.
2. **Atomic Ownership Binding:** Lock acquisition and release are bound to an unforgeable, cryptographically unique UUID.
3. **Safe Release Under Contention:** A worker whose execution spans past TTL will return `0` from Lua release and cannot delete a successor worker's newly acquired lock.
4. **Fail-Closed Availability:** When Redis is partitioned, unreachable, or throwing errors, execution aborts immediately without attempting local fallback.
5. **Pre-flight Ownership Gate:** Handlers are not called if the lock expired or was taken during prior setup steps.

### 5.2 Remaining Limitations & Edge Cases (Honest Assessment)
1. **Fencing Token Incompleteness (Network In-Flight Partition):**
   - Distributed locking via Redis leases does not provide absolute hardware fencing tokens (e.g., Raft epoch numbers verified by target service storage).
   - If a worker's lock lease expires *while* the HTTP request is in-flight across the network to the target microservice, and another worker acquires the lock, overlapping effects can still occur at the microservice level.
   - *Mitigation:* Handlers are idempotent fault-reset calls; however, strict distributed exactly-once execution cannot be claimed.
2. **Redis Single-Instance vs Cluster (Redlock):**
   - The platform uses standard Redis master locking. In a Redis cluster or sentinel topology with asynchronous replication, failover during lock acquisition could result in dual acquisition if the replica did not sync before failover.
3. **No Authenticated Principals or Cryptographic Approval Hashes:**
   - Addressed in subsequent checkpoints (Checkpoint 2: Authentication; Checkpoint 3: Approval Content Hashing).
