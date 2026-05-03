"""Unit tests for TokenRateLimiter token bucket semantics."""

from __future__ import annotations

import pytest

from agentium.ai_gateway.token_rate_limit import TokenRateLimiter


def test_reserve_consumes_tokens():
    clock = iter([0.0, 0.0, 0.0])
    limiter = TokenRateLimiter(
        default_capacity=100, default_refill_per_second=10, clock=lambda: next(clock)
    )
    decision = limiter.reserve("t1", 30)
    assert decision.allowed
    assert decision.available_tokens == 70


def test_reserve_blocks_when_capacity_exhausted():
    times = iter([0.0, 0.0, 0.0])
    limiter = TokenRateLimiter(
        default_capacity=20, default_refill_per_second=1, clock=lambda: next(times)
    )
    first = limiter.reserve("t1", 20)
    assert first.allowed
    blocked = limiter.reserve("t1", 5)
    assert blocked.allowed is False
    assert blocked.wait_seconds == pytest.approx(5.0)
    assert blocked.reason == "rate_limited"


def test_refill_restores_capacity_over_time():
    times = iter([0.0, 0.0, 5.0, 5.0])
    limiter = TokenRateLimiter(
        default_capacity=20, default_refill_per_second=2, clock=lambda: next(times)
    )
    limiter.reserve("t1", 20)
    decision = limiter.reserve("t1", 5)
    assert decision.allowed
    assert decision.available_tokens == pytest.approx(5.0)


def test_configure_overrides_apply_per_tenant():
    times = iter([0.0, 0.0, 0.0])
    limiter = TokenRateLimiter(
        default_capacity=10, default_refill_per_second=1, clock=lambda: next(times)
    )
    limiter.configure("vip", capacity=100, refill_per_second=10)
    decision = limiter.reserve("vip", 50)
    assert decision.allowed
    assert decision.available_tokens == 50


def test_refund_returns_tokens_to_bucket():
    clock = iter([0.0, 0.0, 0.0, 0.0])
    limiter = TokenRateLimiter(
        default_capacity=50, default_refill_per_second=1, clock=lambda: next(clock)
    )
    limiter.reserve("t1", 40)
    limiter.refund("t1", 10)
    snap = limiter.snapshot("t1")
    assert snap.available_tokens == 20
