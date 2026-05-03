"""Integration test: DLP wired through ToolRegistry blocks tool output."""

from __future__ import annotations

import pytest

from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyDocument, PolicyEngine, PolicyRule
from agentium.models.context import DecisionType, RequestContext
from agentium.security.dlp_audit_stage import DLP_AUDIT_STAGE_TOOL_OUTPUT_POST
from agentium.security.dlp_classifier import DLPClassifier
from agentium.shared.errors import PolicyDeniedError
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


def _allow_tool_engine(tool_name: str) -> PolicyEngine:
    return PolicyEngine(
        policy=PolicyDocument(
            version="t",
            default_decision=DecisionType.DENY,
            default_reason="default",
            rules=[
                PolicyRule(
                    id="allow",
                    decision=DecisionType.ALLOW,
                    reason="allow",
                    tools=[tool_name],
                )
            ],
        )
    )


@pytest.mark.paper
def test_dlp_blocks_secret_output() -> None:
    """Paper H4: outbound DLP classifier blocks secret payloads before delivery."""

    audit = InMemoryAuditSink()
    registry = ToolRegistry(
        policy_engine=_allow_tool_engine("leak"),
        budget_ledger=BudgetLedger(
            tenant_budgets={
                "t1": TenantBudget(
                    token_limit=10000, cost_limit=10.0, max_concurrency=4
                )
            }
        ),
        audit_sink=audit,
        approval_gate=ApprovalGate(),
        dlp_classifier=DLPClassifier(),
    )
    registry.register(
        ToolSpec(
            name="leak",
            capabilities=["read_only"],
            risk_level="low",
            handler=lambda args: {
                "body": "-----BEGIN OPENSSH PRIVATE KEY-----\nABC\n-----END OPENSSH PRIVATE KEY-----"
            },
        )
    )
    context = RequestContext(
        request_id="r",
        run_id="run-leak",
        tenant_id="t1",
        user_id="u1",
        trace_id="trace",
    )
    try:
        registry.execute(context=context, name="leak", args={})
        raised = False
    except PolicyDeniedError:
        raised = True
    assert raised is True
    events = [e.event_type for e in audit.query()]
    assert "dlp_blocked" in events
    blocked_payload = next(
        e.payload for e in audit.query() if e.event_type == "dlp_blocked"
    )
    assert blocked_payload.get("dlp_stage") == DLP_AUDIT_STAGE_TOOL_OUTPUT_POST
    hits_payload = next(
        e.payload for e in audit.query() if e.event_type == "dlp_hits_detected"
    )
    assert hits_payload.get("dlp_stage") == DLP_AUDIT_STAGE_TOOL_OUTPUT_POST
