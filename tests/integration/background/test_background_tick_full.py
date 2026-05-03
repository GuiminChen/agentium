"""Integration tests for :meth:`BackgroundDaemon.tick_full`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentium.background.background_daemon import BackgroundDaemon
from agentium.background.event_ingestor import EventIngestor
from agentium.background.memory_consolidator import MemoryConsolidator
from agentium.background.notify_bridge import NotifyBridge
from agentium.background.trigger_planner import TriggerPlanner, TriggerRule
from agentium.channels.null_adapter import NullChannelAdapter
from agentium.channels.outbound_orchestrator import (
    OutboundOrchestrator,
    RateLimit,
)
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
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


def _build_daemon(*, with_consolidation: bool):
    audit = InMemoryAuditSink()
    policy = PolicyEngine(policy=[])
    approvals = ApprovalGate()
    null_adapter = NullChannelAdapter()
    orchestrator = OutboundOrchestrator(
        adapters={null_adapter.name: null_adapter},
        audit_sink=audit,
        rate_limit=RateLimit(max_per_window=10, window_seconds=60.0),
    )
    bridge = NotifyBridge(orchestrator=orchestrator)
    ingestor = EventIngestor()
    planner = TriggerPlanner(
        [
            TriggerRule(
                topic="memory.fresh_item",
                action="background.notify_memory_update",
                risk="low",
                description="memory update",
            ),
            TriggerRule(
                topic="channel.delivery_failed",
                action="background.escalate_channel_failure",
                risk="high",
                description="channel failure",
            ),
        ]
    )
    memory_service = MemoryService(backend=InMemoryBackend(), audit_sink=audit)
    consolidator = MemoryConsolidator(
        memory_service=memory_service, promotion_threshold_seconds=10.0
    )

    def ctx_factory() -> RequestContext:
        return _ctx()

    daemon = BackgroundDaemon(
        approval_service=approvals,
        audit_sink=audit,
        policy_engine=policy,
        interval_seconds=60.0,
        event_ingestor=ingestor,
        trigger_planner=planner,
        memory_consolidator=consolidator if with_consolidation else None,
        notify_bridge=bridge,
        consolidation_context_factory=ctx_factory if with_consolidation else None,
    )
    return daemon, ingestor, null_adapter, audit, memory_service


def test_tick_full_dispatches_low_risk_suggestion() -> None:
    daemon, ingestor, null_adapter, audit, _ = _build_daemon(with_consolidation=False)
    ingestor.submit("memory.fresh_item", {"tenant_id": "tenant-A", "title": "ok"})

    report = daemon.tick_full()

    assert report.events_drained == 1
    assert len(report.suggestions) == 1
    assert "background.notify_memory_update" in report.dispatched
    assert null_adapter.sent
    assert any(
        rec.event_type == "background_suggestion_dispatched" for rec in audit.query()
    )


def test_tick_full_holds_high_risk_for_approval() -> None:
    daemon, ingestor, null_adapter, audit, _ = _build_daemon(with_consolidation=False)
    ingestor.submit(
        "channel.delivery_failed",
        {"tenant_id": "tenant-A", "channel": "web"},
    )

    report = daemon.tick_full()

    assert report.suggestions == []
    assert len(report.approval_required) == 1
    assert null_adapter.sent == []
    assert any(
        rec.event_type == "background_action_requires_approval" for rec in audit.query()
    )


def test_tick_full_runs_consolidation_when_enabled() -> None:
    daemon, ingestor, _, _, memory_service = _build_daemon(with_consolidation=True)

    backend = memory_service._backend  # type: ignore[attr-defined]
    backend.append(
        MemoryRecord(
            tenant_id="tenant-A",
            layer=MemoryLayer.SHORT,
            key="aged",
            payload={"v": 1},
            created_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
    )

    report = daemon.tick_full()

    assert report.consolidation is not None
    assert report.consolidation.promoted_to_mid == 1
    mid = memory_service.recall(_ctx(), MemoryLayer.MID)
    assert any(r.key == "aged" for r in mid)
