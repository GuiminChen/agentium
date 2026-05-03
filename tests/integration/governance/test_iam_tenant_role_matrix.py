"""Phase 3 / phased-delivery §3: 3 tenants x 3 roles policy matrix (deterministic allow/deny + audit)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.core.agent_runtime import AgentRuntime, RuntimeStatus
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.models.context import RequestContext
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


def _policy_matrix(tmp_path: Path) -> Path:
    """Exactly one (tenant, role) cell allows matrix_tool per tenant; all others default deny."""
    p = tmp_path / "iam-matrix.yaml"
    p.write_text(
        "\n".join(
            [
                "version: p0-matrix",
                "default_decision: deny",
                "default_reason: matrix default deny",
                "rules:",
                "  - id: t1-admin",
                "    decision: allow",
                "    reason: t1 admin lane",
                "    tools: [matrix_tool]",
                "    roles: [admin]",
                "    tenants: [t1]",
                "  - id: t2-ops",
                "    decision: allow",
                "    reason: t2 ops lane",
                "    tools: [matrix_tool]",
                "    roles: [user_ops]",
                "    tenants: [t2]",
                "  - id: t3-analyst",
                "    decision: allow",
                "    reason: t3 analyst lane",
                "    tools: [matrix_tool]",
                "    roles: [analyst]",
                "    tenants: [t3]",
            ]
        ),
        encoding="utf-8",
    )
    return p


def _tenants() -> list[str]:
    return ["t1", "t2", "t3"]


def _roles() -> list[str]:
    return ["admin", "user_ops", "analyst"]


def _allowed_pairs() -> set[tuple[str, str]]:
    return {("t1", "admin"), ("t2", "user_ops"), ("t3", "analyst")}


@pytest.mark.integration
def test_three_tenants_three_roles_policy_matrix_and_audit(tmp_path: Path) -> None:
    policy_path = _policy_matrix(tmp_path)
    engine = PolicyEngine.load(policy_path)
    tenants = _tenants()
    ledger = BudgetLedger({t: TenantBudget(token_limit=5000, cost_limit=50.0, max_concurrency=4) for t in tenants})
    audit = InMemoryAuditSink()
    registry = ToolRegistry(policy_engine=engine, budget_ledger=ledger, audit_sink=audit)
    registry.register(
        ToolSpec(
            name="matrix_tool",
            capabilities=["utility"],
            risk_level="low",
            handler=lambda args: {"cell": args.get("cell")},
        )
    )
    runtime = AgentRuntime(tool_registry=registry)

    allowed = _allowed_pairs()
    for tenant_id in tenants:
        for role in _roles():
            cell = f"{tenant_id}:{role}"
            ctx = RequestContext(
                request_id=f"req-{cell}",
                run_id=f"run-{cell}",
                tenant_id=tenant_id,
                user_id="u-matrix",
                trace_id=f"tr-{cell}",
                role=role,
                deployment_mode="prod",
            )
            result = runtime.run_turn(context=ctx, tool_name="matrix_tool", args={"cell": cell})
            events = audit.query(run_id=ctx.run_id)

            if (tenant_id, role) in allowed:
                assert result.status == RuntimeStatus.COMPLETED, cell
                assert result.error_code is None
                assert any(e.event_type == "tool_executed" for e in events), cell
            else:
                assert result.status == RuntimeStatus.BLOCKED, cell
                assert result.error_code == "policy_denied"
                assert any(e.event_type == "policy_decision" for e in events), cell
