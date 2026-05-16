"""Execution-plane gate: high-risk tools on code-exec-mcp tier (P0 / Anthropic Brain–Hands)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.models.context import RequestContext
from agentium.shared.errors import PolicyDeniedError
from agentium.tools.tool_registry import ToolExecutionResult, ToolRegistry, ToolSpec


def _open_policy(tmp_path: Path) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: allow",
                "default_reason: ok",
                "rules: []",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_code_exec_denies_high_risk_for_user_when_gate_on(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_open_policy(tmp_path))
    audit = InMemoryAuditSink()
    reg = ToolRegistry(
        policy_engine=engine,
        budget_ledger=BudgetLedger({"t": TenantBudget(100, 1.0, 4)}),
        audit_sink=audit,
        approval_gate=ApprovalGate(),
        deny_high_risk_tools_under_code_exec_tier=True,
    )
    reg.register(
        ToolSpec(
            name="db_export",
            capabilities=["db"],
            risk_level="high",
            handler=lambda a: {"ok": True},
        )
    )
    ctx = RequestContext(
        request_id="r1",
        run_id="run1",
        tenant_id="t",
        user_id="u1",
        trace_id="tr1",
        role="user",
        mcp_execution_tier="code-exec-mcp",
    )
    with pytest.raises(PolicyDeniedError):
        reg.execute(ctx, "db_export", {"dataset": "x"})
    events = [e for e in audit.query(tenant_id="t") if e.event_type == "execution_plane_tool_denied"]
    assert len(events) == 1
    assert events[0].payload.get("tool_name") == "db_export"


def test_code_exec_allows_high_risk_for_admin_when_gate_on(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_open_policy(tmp_path))
    reg = ToolRegistry(
        policy_engine=engine,
        budget_ledger=BudgetLedger({"t": TenantBudget(100, 1.0, 4)}),
        audit_sink=InMemoryAuditSink(),
        approval_gate=ApprovalGate(),
        deny_high_risk_tools_under_code_exec_tier=True,
    )
    reg.register(
        ToolSpec(
            name="db_export",
            capabilities=["db"],
            risk_level="high",
            handler=lambda a: {"ok": True},
        )
    )
    ctx = RequestContext(
        request_id="r1",
        run_id="run1",
        tenant_id="t",
        user_id="u1",
        trace_id="tr1",
        role="admin",
        mcp_execution_tier="code-exec-mcp",
    )
    out: ToolExecutionResult = reg.execute(ctx, "db_export", {"dataset": "x"})
    assert out.output["ok"] is True


def test_direct_tier_allows_high_risk_for_user_when_gate_on(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_open_policy(tmp_path))
    reg = ToolRegistry(
        policy_engine=engine,
        budget_ledger=BudgetLedger({"t": TenantBudget(100, 1.0, 4)}),
        audit_sink=InMemoryAuditSink(),
        approval_gate=ApprovalGate(),
        deny_high_risk_tools_under_code_exec_tier=True,
    )
    reg.register(
        ToolSpec(
            name="db_export",
            capabilities=["db"],
            risk_level="high",
            handler=lambda a: {"ok": True},
        )
    )
    ctx = RequestContext(
        request_id="r1",
        run_id="run1",
        tenant_id="t",
        user_id="u1",
        trace_id="tr1",
        role="user",
        mcp_execution_tier="direct-tool",
    )
    out = reg.execute(ctx, "db_export", {"dataset": "x"})
    assert out.output["ok"] is True
