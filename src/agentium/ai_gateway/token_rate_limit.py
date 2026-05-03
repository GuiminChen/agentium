"""Token-bucket rate limiter for the AI gateway.

The router needs a backpressure-friendly limit on tokens dispatched per
tenant per minute.  We use a classic token bucket because:

- it allows short bursts inside the bucket capacity;
- it falls back to deterministic refill so tests are repeatable;
- consumed tokens can exceed remaining capacity and we report the deficit
  so callers can choose to wait, downgrade, or drop the request.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of a rate-limit check."""

    allowed: bool
    tenant_id: str
    requested_tokens: int
    available_tokens: float
    wait_seconds: float = 0.0
    reason: str = ""


@dataclass
class _Bucket:
    capacity: float
    refill_per_second: float
    tokens: float = field(init=False)
    last_refill: float = field(default=0.0)

    def __post_init__(self) -> None:
        self.tokens = self.capacity


class TokenRateLimiter:
    """Token bucket per tenant.

    Args:
        default_capacity: tokens that fit in a freshly minted bucket.
        default_refill_per_second: refill rate when no tenant override exists.
        clock: optional monotonic clock; tests inject a deterministic one.
    """

    def __init__(
        self,
        *,
        default_capacity: float = 60_000.0,
        default_refill_per_second: float = 1_000.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if default_capacity <= 0:
            raise ValueError("default_capacity must be positive")
        if default_refill_per_second <= 0:
            raise ValueError("default_refill_per_second must be positive")
        self._default_capacity = default_capacity
        self._default_refill = default_refill_per_second
        self._clock = clock
        self._overrides: Dict[str, _Bucket] = {}
        self._buckets: Dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def configure(
        self,
        tenant_id: str,
        *,
        capacity: float,
        refill_per_second: float,
    ) -> None:
        if capacity <= 0 or refill_per_second <= 0:
            raise ValueError("capacity and refill_per_second must be positive")
        bucket = _Bucket(capacity=capacity, refill_per_second=refill_per_second)
        bucket.last_refill = self._clock()
        with self._lock:
            self._overrides[tenant_id] = bucket
            self._buckets.pop(tenant_id, None)

    def reserve(self, tenant_id: str, tokens: int) -> RateLimitDecision:
        if tokens <= 0:
            raise ValueError("tokens must be positive")
        with self._lock:
            bucket = self._get_bucket(tenant_id)
            self._refill(bucket)
            if bucket.tokens >= tokens:
                bucket.tokens -= tokens
                return RateLimitDecision(
                    allowed=True,
                    tenant_id=tenant_id,
                    requested_tokens=tokens,
                    available_tokens=bucket.tokens,
                )
            deficit = tokens - bucket.tokens
            wait = deficit / bucket.refill_per_second if bucket.refill_per_second > 0 else 0.0
            return RateLimitDecision(
                allowed=False,
                tenant_id=tenant_id,
                requested_tokens=tokens,
                available_tokens=bucket.tokens,
                wait_seconds=wait,
                reason="rate_limited",
            )

    def refund(self, tenant_id: str, tokens: int) -> None:
        if tokens <= 0:
            return
        with self._lock:
            bucket = self._get_bucket(tenant_id)
            bucket.tokens = min(bucket.capacity, bucket.tokens + tokens)

    def snapshot(self, tenant_id: str) -> RateLimitDecision:
        with self._lock:
            bucket = self._get_bucket(tenant_id)
            self._refill(bucket)
            return RateLimitDecision(
                allowed=True,
                tenant_id=tenant_id,
                requested_tokens=0,
                available_tokens=bucket.tokens,
            )

    def _get_bucket(self, tenant_id: str) -> _Bucket:
        bucket = self._buckets.get(tenant_id)
        if bucket is not None:
            return bucket
        override = self._overrides.get(tenant_id)
        if override is not None:
            bucket = _Bucket(
                capacity=override.capacity,
                refill_per_second=override.refill_per_second,
            )
        else:
            bucket = _Bucket(
                capacity=self._default_capacity,
                refill_per_second=self._default_refill,
            )
        bucket.last_refill = self._clock()
        self._buckets[tenant_id] = bucket
        return bucket

    def _refill(self, bucket: _Bucket) -> None:
        now = self._clock()
        elapsed = max(0.0, now - bucket.last_refill)
        if elapsed <= 0:
            return
        bucket.tokens = min(
            bucket.capacity, bucket.tokens + elapsed * bucket.refill_per_second
        )
        bucket.last_refill = now


__all__ = ["RateLimitDecision", "TokenRateLimiter"]
