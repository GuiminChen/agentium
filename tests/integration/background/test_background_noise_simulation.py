"""Integration-style noise simulation: high ingest rate triggers background_noise_tripwire (§4)."""

from __future__ import annotations

import pytest

from agentium.background.background_daemon import BackgroundDaemon
from agentium.background.event_ingestor import EventIngestor
from agentium.background.trigger_planner import TriggerPlanner
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine, PolicyDocument
from agentium.models.context import DecisionType


@pytest.mark.integration
def test_background_noise_tripwire_integration_simulates_burst_over_threshold(tmp_path) -> None:
    """Maps to security-acceptance-checklist §4 (killswitch under high event rate); uses injectable clock."""

    policy_engine = PolicyEngine(
        policy=PolicyDocument(
            version="t1",
            default_decision=DecisionType.DENY,
            default_reason="default",
            rules=[],
        )
    )
    t0 = 5000.0
    ing = EventIngestor(clock=lambda: t0, rate_window_seconds=1.0)
    planner = TriggerPlanner(rules=[])
    sink = InMemoryAuditSink()
    daemon = BackgroundDaemon(
        approval_service=ApprovalGate(),
        audit_sink=sink,
        policy_engine=policy_engine,
        interval_seconds=60.0,
        event_ingestor=ing,
        trigger_planner=planner,
        noise_rps_pause=50.0,
    )
    for i in range(55):
        ing.submit("noise.sim", {"tenant_id": "t1", "i": i}, dedupe_key=str(i))
    daemon.tick_full()
    assert daemon.paused
    assert any(e.event_type == "background_noise_tripwire" for e in sink.query())
