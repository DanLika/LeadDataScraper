"""In-process TTL cache for /stats response.

/stats fetches all leads (narrow column set), builds a pandas DataFrame,
and runs value_counts per request. At 100 rps with even a few thousand
rows the DataFrame allocation alone saturates a worker. This cache turns
the load from "rebuild per request" into "rebuild once per TTL", with
explicit invalidation hooks for write paths so the cached value never
lies for longer than ~one batch of writes.

Single-tenant deployment: one operator, ~thousands of leads, multi-worker
uvicorn at most. Per-process cache is fine — at uvicorn --workers N you
just pay N builds per TTL instead of one. A shared Redis would tighten
that to 1 across workers but adds infra cost the spec ruled out.

Stampede protection: an `asyncio.Lock` guards the build path. If 100
concurrent /stats requests arrive at the same expiry tick, only one runs
build_fn; the rest await the lock and read the freshly populated value
on the second check.

Thread-safety: per-process, single-event-loop only. uvicorn workers run
their own event loop per process, so each gets its own _StatsCache
instance via the module-level singleton — no cross-loop sharing.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Optional


class _StatsCache:
    def __init__(self, ttl_seconds: float = 60.0):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()
        self._payload: Optional[Any] = None
        self._expires_at: float = 0.0

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def _fresh(self) -> bool:
        return self._payload is not None and time.monotonic() < self._expires_at

    async def get(self, build_fn: Callable[[], Awaitable[Any]]) -> Any:
        """Return cached payload if fresh; otherwise rebuild via
        `build_fn()` under a lock so concurrent callers don't all
        rebuild. `build_fn` must be an async callable that returns
        the value to cache."""
        if self._fresh():
            return self._payload
        async with self._lock:
            # Double-check: another task may have rebuilt while we
            # waited on the lock. Without this, every contender after
            # the first would rebuild redundantly.
            if self._fresh():
                return self._payload
            payload = await build_fn()
            self._payload = payload
            self._expires_at = time.monotonic() + self._ttl
            return payload

    def invalidate(self) -> None:
        """Mark the cache stale immediately. Next `get()` will rebuild.

        Cheap and lock-free — worst case is one in-flight request reads
        the about-to-be-flushed value, which is acceptable for a cache.
        Call after any write that the operator would expect to see
        reflected in /stats: /upload completion, orchestrator job
        finish, manual lead edits via /process-lead.
        """
        self._payload = None
        self._expires_at = 0.0


# Module-level singleton. Each uvicorn worker process gets its own
# (workers do not share Python memory). 60-second TTL matches the spec.
stats_cache = _StatsCache(ttl_seconds=60.0)
