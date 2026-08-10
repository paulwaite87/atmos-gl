#!/usr/bin/env python3
"""In-memory sliding-window rate limiting (issue #307): a small reusable piece
applied via a thin per-router dependency wherever a route needs throttling
(routes/auth.py's /login and /callback, routes/me_settings.py's settings writes and
account deletion) -- not bespoke counters per route. No new dependency, no DB table:
map_api runs as a single instance today, so per-process in-memory state needs neither
to survive restarts nor to be shared across replicas; revisit only if that changes."""
import logging
import time
from collections import defaultdict
from typing import Callable

from fastapi import HTTPException

logger = logging.getLogger("atmos_gl.lib.rate_limit")


class RateLimiter:
    """One instance per class of endpoint (e.g. "auth" vs "settings writes"), shared
    process-wide across every request that calls .enforce() on it -- construct a
    separate instance per distinct limit/window pair, never reuse one across unrelated
    endpoint classes."""

    def __init__(
        self, max_requests: int, window_seconds: float, *, clock: Callable[[], float] = time.monotonic,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock = clock
        self._hits: dict[str, list[float]] = defaultdict(list)

    def enforce(self, key: str) -> None:
        """Records this hit against `key`; raises HTTPException(429) with a
        Retry-After header if `key` was already at the limit within the current
        sliding window (a rejected attempt is not itself recorded as a hit, so a
        caller stuck retrying past the limit still recovers once the window rolls
        forward)."""
        now = self._clock()
        window_start = now - self.window_seconds
        hits = self._hits[key]
        while hits and hits[0] < window_start:
            hits.pop(0)
        if len(hits) >= self.max_requests:
            retry_after = hits[0] + self.window_seconds - now
            logger.debug(f"rate limit exceeded for key={key!r} ({self.max_requests}/{self.window_seconds}s)")
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": str(max(1, int(retry_after) + 1))},
            )
        hits.append(now)


def rate_limiter_dependency(max_requests: int, window_seconds: float) -> Callable[[], RateLimiter]:
    """Builds one process-wide RateLimiter and returns a FastAPI dependency-factory
    function for it -- Depends(the returned function) always resolves to the SAME
    instance (so hits actually accumulate across requests), and the function itself
    is overridable per-test via app.dependency_overrides[the returned function]. Each
    router that needs rate limiting still writes its own thin wrapper on top of this
    (deriving the key differs genuinely by endpoint class -- IP pre-auth, user id
    post-auth -- so that part isn't shared), but the "one shared limiter instance
    behind a stable DI seam" part is."""
    limiter = RateLimiter(max_requests, window_seconds)

    def get_limiter() -> RateLimiter:
        return limiter

    return get_limiter
