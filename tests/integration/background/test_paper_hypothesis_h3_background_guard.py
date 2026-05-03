"""Paper H3: background-plane actions are gated; policy deny produces auditable blocks."""

from __future__ import annotations

import pytest

from agentium.background.background_daemon import BackgroundDaemon
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyDocument, PolicyEngine
from agentium.models.context import DecisionType, RequestContext


@pytest.mark.paper
def test_paper_hypothesis_h3_background_action_blocked_when_policy_denies() -> None:
    """When policy does not ALLOW a background tool, evaluate_action returns False and audits."""

    audit = InMemoryAuditSink()
    policy = PolicyEngine(
        policy=PolicyDocument(
            version="paper_h3",
            default_decision=DecisionType.DENY,
            default_reason="default deny",
            rules=[],
        )
    )
    daemon = BackgroundDaemon(
        approval_service=ApprovalGate(),
        audit_sink=audit,
        policy_engine=policy,
        interval_seconds=30.0,
    )
    ctx = RequestContext(
        request_id="r",
        run_id="run-bg",
        tenant_id="t1",
        user_id="u1",
        trace_id="trace-bg",
    )
    allowed = daemon.evaluate_action(ctx, "notify_external", {})
    assert allowed is False
    # Audit is correlated with the request run_id (not the daemon default "_background").
    events = [e.event_type for e in audit.query(run_id="run-bg")]
    assert "background_action_blocked" in events
