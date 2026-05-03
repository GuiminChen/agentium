"""Unit tests for MemoryService."""

from __future__ import annotations

import pytest

from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.memory import MemoryLayer, MemoryService
from agentium.memory.backends.inmemory_backend import InMemoryBackend
from agentium.models.context import RequestContext
from agentium.shared.errors import PolicyDeniedError


@pytest.fixture()
def context() -> RequestContext:
    return RequestContext(
        request_id="r1",
        run_id="run-1",
        tenant_id="tenant-a",
        user_id="user-a",
        trace_id="trace",
    )


def test_remember_and_recall(context: RequestContext) -> None:
    service = MemoryService(InMemoryBackend())
    service.remember(context, layer=MemoryLayer.SHORT, key="k1", payload={"value": 1})
    records = service.recall(context, layer=MemoryLayer.SHORT)
    assert len(records) == 1
    assert records[0].payload == {"value": 1}


@pytest.mark.paper
def test_cross_tenant_blocked(context: RequestContext) -> None:
    """Paper H5: cross-tenant memory access is hard-denied with explicit audit."""

    sink = InMemoryAuditSink()
    service = MemoryService(InMemoryBackend(), audit_sink=sink)
    with pytest.raises(PolicyDeniedError):
        service.recall(context, layer=MemoryLayer.SHORT, target_tenant_id="other")
    events = [e.event_type for e in sink.query()]
    assert "memory_cross_tenant_blocked" in events


def test_purge_only_owns_tenant(context: RequestContext) -> None:
    backend = InMemoryBackend()
    service = MemoryService(backend)
    service.remember(context, layer=MemoryLayer.SHORT, key="k", payload={"v": 1})
    removed = service.purge(context)
    assert removed == 1
