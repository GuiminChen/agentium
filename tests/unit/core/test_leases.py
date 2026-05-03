"""Unit tests for LeaseManager."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agentium.core.leases import LeaseManager


def test_lease_acquire_renew_release() -> None:
    now = [datetime(2024, 1, 1, tzinfo=timezone.utc)]
    manager = LeaseManager(clock=lambda: now[0])
    lease = manager.acquire("l1", holder="run-1", tenant_id="t1", ttl_seconds=10)
    assert manager.get("l1") is lease
    renewed = manager.renew("l1", ttl_seconds=20)
    assert renewed is not None
    assert renewed.renew_count == 1
    assert manager.release("l1") is True
    assert manager.get("l1") is None


def test_lease_sweep_expired() -> None:
    now = [datetime(2024, 1, 1, tzinfo=timezone.utc)]
    manager = LeaseManager(clock=lambda: now[0])
    manager.acquire("l1", holder="run-1", tenant_id="t1", ttl_seconds=1)
    manager.acquire("l2", holder="run-2", tenant_id="t1", ttl_seconds=1000)
    now[0] = now[0] + timedelta(seconds=10)
    expired = manager.sweep_expired()
    assert {l.lease_id for l in expired} == {"l1"}
    assert manager.get("l1") is None
    assert manager.get("l2") is not None


def test_lease_invalid_ttl() -> None:
    manager = LeaseManager()
    with pytest.raises(ValueError):
        manager.acquire("l1", holder="r", tenant_id="t", ttl_seconds=0)
