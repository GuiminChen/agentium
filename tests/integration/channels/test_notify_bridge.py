"""Integration test: NotifyBridge → OutboundOrchestrator → NullChannelAdapter."""

from __future__ import annotations

from agentium.background.notify_bridge import NotifyBridge, NotifyRequest
from agentium.channels import (
    ChannelKind,
    NullChannelAdapter,
    OutboundOrchestrator,
    RateLimit,
)
from agentium.governance.audit_lineage import InMemoryAuditSink


def _bridge(rate_limit=None):
    audit = InMemoryAuditSink()
    null = NullChannelAdapter()
    orch = OutboundOrchestrator(
        adapters={null.name: null},
        audit_sink=audit,
        rate_limit=rate_limit,
    )
    return NotifyBridge(orch), null, audit


def test_notify_bridge_dispatches_through_orchestrator():
    bridge, channel, audit = _bridge()
    result = bridge.notify(
        NotifyRequest(
            tenant_id="t1",
            title="Suggested action",
            body="Approve job 42",
            recipient="ops@example.com",
            kind=ChannelKind.NULL,
            run_id="run-bridge",
            metadata={"trigger": "background"},
        )
    )
    assert result.delivered
    assert channel.sent[0].subject == "Suggested action"
    assert "channel_delivered" in {e.event_type for e in audit.query()}


def test_notify_bridge_respects_rate_limit():
    bridge, _, _ = _bridge(rate_limit=RateLimit(max_per_window=1, window_seconds=60.0))
    bridge.notify(
        NotifyRequest(
            tenant_id="t1",
            title="t",
            body="b",
            recipient="ops@example.com",
        )
    )
    second = bridge.notify(
        NotifyRequest(
            tenant_id="t1",
            title="t",
            body="b",
            recipient="ops@example.com",
        )
    )
    assert second.skipped and second.skipped[0]["reason"] == "rate_limited"
