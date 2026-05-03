"""Paper H2: high-risk paths must not execute without a valid ApprovalGate decision."""

from __future__ import annotations

import pytest

from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.models.context import RequestContext
from agentium.shared.errors import ApprovalRequiredError, PolicyDeniedError
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


def _policy(tmp_path, admin_tool: str = "db_export") -> object:
    p = tmp_path / "p.yaml"
    p.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: deny",
                "default_reason: denied by default",
                "rules:",
                "  - id: require-approval",
                "    decision: require_approval",
                "    reason: export requires approval",
                f"    tools: [{admin_tool}]",
                "    roles: [admin]",
            ]
        ),
        encoding="utf-8",
    )
    return PolicyEngine.load(p)


@pytest.mark.paper
def test_paper_hypothesis_h2_wrong_approval_id_does_not_execute(tmp_path) -> None:
    """Resume with a random approval_id must not complete the tool call."""

    engine = _policy(tmp_path)
    ledger = BudgetLedger({"t": TenantBudget(1000, 10.0, 1)})
    audit = InMemoryAuditSink()
    gate = ApprovalGate()
    reg = ToolRegistry(
        policy_engine=engine,
        budget_ledger=ledger,
        audit_sink=audit,
        approval_gate=gate,
    )
    reg.register(
        ToolSpec(name="db_export", capabilities=["db"], risk_level="high", handler=lambda a: {"ok": True})
    )
    ctx = RequestContext(
        request_id="r1",
        run_id="run-x",
        tenant_id="t",
        user_id="u",
        trace_id="tr",
        role="admin",
    )
    with pytest.raises(ApprovalRequiredError) as first:
        reg.execute(ctx, "db_export", {})
    aid = first.value.approval_id
    assert aid
    with pytest.raises(ApprovalRequiredError):
        reg.execute(
            ctx,
            "db_export",
            {},
            approval_id="00000000-0000-0000-0000-000000000000",
        )


@pytest.mark.paper
def test_paper_hypothesis_h2_args_mismatch_blocks_after_approval(tmp_path) -> None:
    """Changing args after approval request must block execution."""

    engine = _policy(tmp_path)
    ledger = BudgetLedger({"t": TenantBudget(1000, 10.0, 1)})
    gate = ApprovalGate()
    reg = ToolRegistry(
        policy_engine=engine,
        budget_ledger=ledger,
        audit_sink=InMemoryAuditSink(),
        approval_gate=gate,
    )
    reg.register(
        ToolSpec(name="db_export", capabilities=["db"], risk_level="high", handler=lambda a: {"ds": a["dataset"]})
    )
    ctx = RequestContext(
        request_id="r1",
        run_id="run-x",
        tenant_id="t",
        user_id="u",
        trace_id="tr",
        role="admin",
    )
    with pytest.raises(ApprovalRequiredError) as first:
        reg.execute(ctx, "db_export", {"dataset": "A"})
    aid = first.value.approval_id
    gate.approve(aid, approver_id="boss", comment="ok")
    with pytest.raises(ApprovalRequiredError, match="args mismatch"):
        reg.execute_after_approval(ctx, "db_export", aid, {"dataset": "B"})


@pytest.mark.paper
def test_paper_hypothesis_h2_rejected_approval_is_policy_denied(tmp_path) -> None:
    """Rejected approval record must not allow execution."""

    engine = _policy(tmp_path)
    ledger = BudgetLedger({"t": TenantBudget(1000, 10.0, 1)})
    gate = ApprovalGate()
    reg = ToolRegistry(
        policy_engine=engine,
        budget_ledger=ledger,
        audit_sink=InMemoryAuditSink(),
        approval_gate=gate,
    )
    reg.register(
        ToolSpec(name="db_export", capabilities=["db"], risk_level="high", handler=lambda a: {"ok": True})
    )
    ctx = RequestContext(
        request_id="r1",
        run_id="run-x",
        tenant_id="t",
        user_id="u",
        trace_id="tr",
        role="admin",
    )
    with pytest.raises(ApprovalRequiredError) as first:
        reg.execute(ctx, "db_export", {})
    aid = first.value.approval_id
    gate.reject(aid, approver_id="boss", comment="no")
    with pytest.raises(PolicyDeniedError, match="rejected"):
        reg.execute_after_approval(ctx, "db_export", aid, {})
