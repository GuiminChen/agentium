"""Unit tests for tenant-fair scheduler and timeout helper."""

from __future__ import annotations

import time

import pytest

from agentium.core.cancel import CancelToken, CancelledError
from agentium.core.scheduler import (
    BackpressureError,
    TenantFairScheduler,
    TimeoutExceededError,
    run_with_timeout,
)


def test_scheduler_round_robin_between_tenants() -> None:
    scheduler = TenantFairScheduler(max_concurrency_per_tenant=1, global_max_concurrency=4)
    log: list[str] = []

    def make_work(tag):
        def work(token):
            log.append(tag)
            return tag

        return work

    scheduler.submit("a1", "tenant-a", make_work("a1"))
    scheduler.submit("a2", "tenant-a", make_work("a2"))
    scheduler.submit("b1", "tenant-b", make_work("b1"))
    scheduler.run_pending(max_jobs=10)
    assert "a1" in log and "b1" in log


def test_scheduler_backpressure() -> None:
    scheduler = TenantFairScheduler(
        max_concurrency_per_tenant=1, global_max_concurrency=1, max_queue_per_tenant=1
    )
    scheduler.submit("a1", "tenant-a", lambda token: 1)
    with pytest.raises(BackpressureError):
        scheduler.submit("a2", "tenant-a", lambda token: 2)


def test_run_with_timeout_succeeds() -> None:
    result = run_with_timeout(work=lambda token: 7, layer="tool", timeout_seconds=1.0)
    assert result == 7


def test_run_with_timeout_raises() -> None:
    def slow(token: CancelToken):
        for _ in range(20):
            if token.cancelled:
                raise CancelledError(token.reason)
            time.sleep(0.05)

    with pytest.raises(TimeoutExceededError):
        run_with_timeout(work=slow, layer="tool", timeout_seconds=0.2)
