"""Unit tests for :class:`MemoryConsolidator`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentium.background.memory_consolidator import MemoryConsolidator
from agentium.memory.backends.inmemory_backend import InMemoryBackend
from agentium.memory.memory_service import MemoryService
from agentium.memory.types import MemoryLayer, MemoryRecord
from agentium.models.context import RequestContext


def _ctx(tenant: str = "tenant-A") -> RequestContext:
    return RequestContext(
        request_id="req-1",
        run_id="r1",
        tenant_id=tenant,
        user_id="u1",
        trace_id="trace-1",
    )


def _seed(memory: MemoryService, ctx: RequestContext, key: str, payload: dict, age_seconds: float) -> None:
    backend = memory._backend  # type: ignore[attr-defined]
    record = MemoryRecord(
        tenant_id=ctx.tenant_id,
        layer=MemoryLayer.SHORT,
        key=key,
        payload=payload,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    )
    backend.append(record)


def test_consolidate_promotes_aged_short_records_to_mid() -> None:
    memory = MemoryService(backend=InMemoryBackend())
    ctx = _ctx()
    _seed(memory, ctx, "fact", {"value": 1}, age_seconds=600.0)
    consolidator = MemoryConsolidator(memory_service=memory, promotion_threshold_seconds=60.0)

    report = consolidator.consolidate(ctx)
    assert report.promoted_to_mid == 1
    mid = memory.recall(ctx, MemoryLayer.MID)
    assert any(r.key == "fact" and r.payload.get("value") == 1 for r in mid)


def test_consolidate_records_conflict_for_divergent_payloads() -> None:
    memory = MemoryService(backend=InMemoryBackend())
    ctx = _ctx()
    _seed(memory, ctx, "k", {"value": 1}, age_seconds=10.0)
    _seed(memory, ctx, "k", {"value": 2}, age_seconds=5.0)
    consolidator = MemoryConsolidator(memory_service=memory, promotion_threshold_seconds=60.0)

    report = consolidator.consolidate(ctx)
    assert report.duplicates_removed == 1
    assert report.conflicts_recorded == 1
    mid = memory.recall(ctx, MemoryLayer.MID)
    assert any(r.key == "k::conflict" for r in mid)


def test_consolidate_no_promotion_for_fresh_record() -> None:
    memory = MemoryService(backend=InMemoryBackend())
    ctx = _ctx()
    _seed(memory, ctx, "fresh", {"value": 1}, age_seconds=1.0)
    consolidator = MemoryConsolidator(memory_service=memory, promotion_threshold_seconds=60.0)

    report = consolidator.consolidate(ctx)
    assert report.promoted_to_mid == 0
    assert memory.recall(ctx, MemoryLayer.MID) == []
