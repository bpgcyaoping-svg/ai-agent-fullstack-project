from __future__ import annotations

import asyncio
import math
import time

from app.config import RateLimitConfig
from app.core.errors import GatewayError


class TokenBucket:
    def __init__(self, config: RateLimitConfig) -> None:
        self.capacity = float(config.burst)
        self.refill_per_second = config.requests_per_minute / 60.0
        self.tokens = self.capacity
        self.updated_at = time.monotonic()

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.updated_at = now

    def try_acquire(self) -> float | None:
        """Return None when admitted, otherwise seconds until the next token."""
        now = time.monotonic()
        self._refill(now)
        if self.tokens >= 1:
            self.tokens -= 1
            return None
        return (1 - self.tokens) / self.refill_per_second

    def snapshot(self) -> dict[str, float]:
        now = time.monotonic()
        self._refill(now)
        return {
            "capacity": self.capacity,
            "tokens": round(self.tokens, 3),
            "refill_per_second": round(self.refill_per_second, 4),
        }


class ModelRateLimiter:
    """Independent token bucket per model, so one model cannot starve another."""

    def __init__(self, limits: dict[str, RateLimitConfig]) -> None:
        self._buckets = {model: TokenBucket(limit) for model, limit in limits.items()}
        self._lock = asyncio.Lock()

    async def acquire(self, model: str) -> None:
        bucket = self._buckets.get(model)
        if bucket is None:
            return
        async with self._lock:
            wait_seconds = bucket.try_acquire()
        if wait_seconds is not None:
            retry_after = max(1, math.ceil(wait_seconds))
            raise GatewayError(
                f"Rate limit exceeded for model {model!r}; retry in {retry_after}s",
                status_code=429,
                error_type="rate_limit_error",
                code="rate_limit_exceeded",
                headers={"Retry-After": str(retry_after)},
            )

    def snapshot(self) -> dict[str, dict[str, float]]:
        return {model: bucket.snapshot() for model, bucket in self._buckets.items()}
