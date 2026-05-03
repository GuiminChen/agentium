"""Unit tests for the :class:`Mem0Backend` adapter (experimental stub)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest

from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.memory import MemoryLayer, MemoryService
from agentium.memory.backends import Mem0Backend
from agentium.memory.types import MemoryRecord
from agentium.models.context import RequestContext
from agentium.shared.errors import PolicyDeniedError


class _FakeMem0Client:
    """Minimal in-memory fake implementing the ``Mem0Client`` protocol."""

    def __init__(self) -> None:
        self.added: List[Dict[str, Any]] = []
        self.deleted_tenants: List[str] = []

    def add(
        self,
        *,
        tenant_id: str,
        layer: str,
        key: str,
        payload: dict,
        created_at: str,
    ) -> None:
        self.added.append(
            {
                "tenant_id": tenant_id,
                "layer": layer,
                "key": key,
                "payload": dict(payload),
                "created_at": created_at,
            }
        )

    def search(
        self,
        *,
        tenant_id: str,
        layer: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        matches = [
            row
            for row in self.added
            if row["tenant_id"] == tenant_id and row["layer"] == layer
        ]
        return matches[-limit:]

    def delete(self, *, tenant_id: str) -> int:
        before = len(self.added)
        self.added = [row for row in self.added if row["tenant_id"] != tenant_id]
        self.deleted_tenants.append(tenant_id)
        return before - len(self.added)


def _context(tenant: str = "tenant-a") -> RequestContext:
    return RequestContext(
        request_id="r1",
        run_id="run-1",
        tenant_id=tenant,
        user_id="user-1",
        trace_id="trace",
    )


def test_backend_without_client_raises_on_write() -> None:
    backend = Mem0Backend()
    assert backend.is_configured is False

    record = MemoryRecord(
        tenant_id="tenant-a",
        layer=MemoryLayer.SHORT,
        key="k1",
        payload={"v": 1},
    )
    with pytest.raises(RuntimeError):
        backend.append(record)


def test_backend_forwards_to_mem0_client() -> None:
    fake = _FakeMem0Client()
    backend = Mem0Backend(client=fake)
    service = MemoryService(backend)

    service.remember(
        _context(),
        layer=MemoryLayer.LONG,
        key="doc-1",
        payload={"text": "hello"},
    )

    assert len(fake.added) == 1
    assert fake.added[0]["tenant_id"] == "tenant-a"
    assert fake.added[0]["layer"] == MemoryLayer.LONG.value
    assert fake.added[0]["payload"] == {"text": "hello"}


def test_backend_roundtrips_records_through_service() -> None:
    fake = _FakeMem0Client()
    service = MemoryService(Mem0Backend(client=fake))

    service.remember(_context(), layer=MemoryLayer.SHORT, key="a", payload={"v": 1})
    service.remember(_context(), layer=MemoryLayer.SHORT, key="b", payload={"v": 2})

    records = service.recall(_context(), layer=MemoryLayer.SHORT, limit=10)
    payloads = [r.payload for r in records]
    assert payloads == [{"v": 1}, {"v": 2}]


def test_memory_service_enforces_tenant_isolation_regardless_of_backend() -> None:
    """The adapter alone does not enforce tenancy; MemoryService does."""

    sink = InMemoryAuditSink()
    service = MemoryService(Mem0Backend(client=_FakeMem0Client()), audit_sink=sink)

    with pytest.raises(PolicyDeniedError):
        service.recall(
            _context(tenant="tenant-a"),
            layer=MemoryLayer.SHORT,
            target_tenant_id="tenant-b",
        )

    events = [e.event_type for e in sink.query()]
    assert "memory_cross_tenant_blocked" in events


def test_purge_tenant_delegates_to_client() -> None:
    fake = _FakeMem0Client()
    service = MemoryService(Mem0Backend(client=fake))

    service.remember(_context(), layer=MemoryLayer.MID, key="k", payload={"v": 1})
    removed = service.purge(_context())

    assert removed == 1
    assert fake.deleted_tenants == ["tenant-a"]


def test_row_translation_fills_defaults_on_partial_rows() -> None:
    class _SparseClient:
        def add(self, **_: Any) -> None:
            raise AssertionError("not expected")

        def search(self, *, tenant_id: str, layer: str, limit: int) -> List[Dict[str, Any]]:
            return [
                {
                    "tenant_id": tenant_id,
                    "layer": layer,
                    "payload": {"ok": True},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ]

        def delete(self, *, tenant_id: str) -> int:
            return 0

    backend = Mem0Backend(client=_SparseClient())
    records = backend.query(tenant_id="tenant-a", layer=MemoryLayer.SHORT, limit=5)
    assert len(records) == 1
    assert records[0].key == "mem0"
    assert records[0].payload == {"ok": True}
