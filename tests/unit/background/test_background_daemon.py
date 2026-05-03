"""Unit tests for BackgroundDaemon."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import pytest

from agentium.background.background_daemon import BackgroundDaemon
from agentium.background.triggers import IntervalTrigger, TriggerEvent
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine, PolicyDocument
from agentium.models.context import DecisionType, RequestContext


@pytest.fixture()
def policy_engine(tmp_path) -> PolicyEngine:
    return PolicyEngine(
        policy=PolicyDocument(
            version="t1",
            default_decision=DecisionType.DENY,
            default_reason="default",
            rules=[],
        )
    )


def test_background_expire_pending_audits(policy_engine: PolicyEngine) -> None:
    sink = InMemoryAuditSink()
    gate = ApprovalGate()
    context = RequestContext(
        request_id="r1",
        run_id="run-1",
        tenant_id="tenant-a",
        user_id="user-a",
        trace_id="trace",
    )
    gate.request_approval(
        context=context,
        tool_name="t",
        reason="r",
        args_hash="h",
        ttl_seconds=1,
    )
    daemon = BackgroundDaemon(
        approval_service=gate,
        audit_sink=sink,
        policy_engine=policy_engine,
        interval_seconds=1.0,
    )
    request_dict = list(gate._requests.values())[0].__dict__  # type: ignore[attr-defined]
    request_dict["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=10)
    daemon.tick()
    events = [e.event_type for e in sink.query()]
    assert "background_approval_expired" in events


def test_background_pause_blocks_dispatch(policy_engine: PolicyEngine) -> None:
    sink = InMemoryAuditSink()
    daemon = BackgroundDaemon(
        approval_service=ApprovalGate(),
        audit_sink=sink,
        policy_engine=policy_engine,
        interval_seconds=1.0,
    )
    fired: List[TriggerEvent] = []
    daemon.add_interval_trigger(
        IntervalTrigger(name="t", interval_seconds=0.0),
        handler=lambda ev: fired.append(ev),
    )
    daemon.pause()
    daemon.tick()
    assert fired == []
    daemon.resume()
    daemon.tick()
    assert len(fired) == 1


def test_background_action_blocked_by_policy(policy_engine: PolicyEngine) -> None:
    sink = InMemoryAuditSink()
    daemon = BackgroundDaemon(
        approval_service=ApprovalGate(),
        audit_sink=sink,
        policy_engine=policy_engine,
        interval_seconds=1.0,
    )
    context = RequestContext(
        request_id="r",
        run_id="run-1",
        tenant_id="t1",
        user_id="u1",
        trace_id="tr",
    )
    allowed = daemon.evaluate_action(context=context, tool_name="anything", args={})
    assert allowed is False
    events = [e.event_type for e in sink.query()]
    assert "background_action_blocked" in events


def test_background_noise_tripwire_pauses_when_ingest_rate_high(policy_engine: PolicyEngine) -> None:
    from agentium.background.event_ingestor import EventIngestor
    from agentium.background.trigger_planner import TriggerPlanner

    ing = EventIngestor(clock=lambda: 1000.0, rate_window_seconds=1.0)
    planner = TriggerPlanner(rules=[])
    sink = InMemoryAuditSink()
    daemon = BackgroundDaemon(
        approval_service=ApprovalGate(),
        audit_sink=sink,
        policy_engine=policy_engine,
        interval_seconds=60.0,
        event_ingestor=ing,
        trigger_planner=planner,
        noise_rps_pause=5.0,
    )
    for _ in range(10):
        ing.submit("noise.test", {"tenant_id": "t1", "i": 1})
    daemon.tick_full()
    assert daemon.paused
    assert any(e.event_type == "background_noise_tripwire" for e in sink.query())
