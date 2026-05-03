"""Unit tests for the ResourceManager quota ledger."""

from __future__ import annotations

import pytest

from agentium.sandbox.resource_manager import (
    ResourceManager,
    ResourceQuota,
    ResourceQuotaExceededError,
)


def test_resource_manager_reserves_within_limit():
    manager = ResourceManager(defaults={"workers": ResourceQuota(hard_limit=2)})
    snap = manager.reserve("tenant-a", "workers")
    assert snap.current == 1 and snap.hard_limit == 2 and not snap.soft_breached


def test_resource_manager_blocks_when_hard_limit_exceeded():
    manager = ResourceManager(defaults={"workers": ResourceQuota(hard_limit=1)})
    manager.reserve("tenant-a", "workers")
    with pytest.raises(ResourceQuotaExceededError):
        manager.reserve("tenant-a", "workers")


def test_resource_manager_marks_soft_limit_breach():
    manager = ResourceManager(
        defaults={"workers": ResourceQuota(hard_limit=10, soft_limit=2)}
    )
    manager.reserve("tenant-a", "workers")
    snap = manager.reserve("tenant-a", "workers", amount=2)
    assert snap.current == 3 and snap.soft_breached is True


def test_resource_manager_release_does_not_underflow():
    manager = ResourceManager(defaults={"workers": ResourceQuota(hard_limit=2)})
    manager.release("tenant-a", "workers")
    assert manager.usage("tenant-a", "workers").current == 0


def test_resource_manager_lease_releases_on_exit():
    manager = ResourceManager(defaults={"workers": ResourceQuota(hard_limit=1)})
    with manager.lease("tenant-a", "workers"):
        with pytest.raises(ResourceQuotaExceededError):
            manager.reserve("tenant-a", "workers")
    assert manager.usage("tenant-a", "workers").current == 0


def test_resource_manager_isolates_tenants():
    manager = ResourceManager(defaults={"workers": ResourceQuota(hard_limit=1)})
    manager.reserve("tenant-a", "workers")
    snap = manager.reserve("tenant-b", "workers")
    assert snap.current == 1
