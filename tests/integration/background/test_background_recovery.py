"""Integration test: background daemon expires approvals during background tick."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agentium.background.background_daemon import BackgroundDaemon
from agentium.governance.approval_gate import ApprovalGate, ApprovalStatus
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyDocument, PolicyEngine
from agentium.models.context import DecisionType, RequestContext


def test_background_recovers_pending_approvals() -> None:
    audit = InMemoryAuditSink()
    gate = ApprovalGate()
    context = RequestContext(
        request_id="r",
        run_id="run-1",
        tenant_id="t1",
        user_id="u1",
        trace_id="trace",
    )
    request = gate.request_approval(
        context=context, tool_name="t", reason="r", args_hash="h", ttl_seconds=1
    )
    request_obj = list(gate._requests.values())[0]  # type: ignore[attr-defined]
    request_obj.expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    daemon = BackgroundDaemon(
        approval_service=gate,
        audit_sink=audit,
        policy_engine=PolicyEngine(
            policy=PolicyDocument(
                version="t",
                default_decision=DecisionType.DENY,
                default_reason="d",
                rules=[],
            )
        ),
        interval_seconds=1.0,
    )
    daemon.tick()
    final = gate.get_request(request.approval_id)
    assert final is not None and final.status == ApprovalStatus.EXPIRED
    events = [e.event_type for e in audit.query()]
    assert "background_approval_expired" in events
