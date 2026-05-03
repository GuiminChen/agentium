"""phased-delivery Phase 3 #6 slice: one success path + one policy_denied path with auditable outcomes."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.core.agent_runtime import AgentRuntime, RuntimeStatus
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.models.context import RequestContext
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


def _policy_allow_probe(tmp_path: Path) -> Path:
    p = tmp_path / "playbook-ok.yaml"
    p.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: deny",
                "default_reason: denied",
                "rules:",
                "  - id: allow-probe",
                "    decision: allow",
                "    reason: ok",
                "    tools: [playbook_probe]",
                "    roles: [admin]",
            ]
        ),
        encoding="utf-8",
    )
    return p


@pytest.mark.integration
def test_playbook_success_turn_has_completed_status_and_audit(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_policy_allow_probe(tmp_path))
    audit = InMemoryAuditSink()
    registry = ToolRegistry(
        policy_engine=engine,
        budget_ledger=BudgetLedger({"tx": TenantBudget(5000, 50.0, 4)}),
        audit_sink=audit,
    )
    registry.register(
        ToolSpec(
            name="playbook_probe",
            capabilities=["utility"],
            risk_level="low",
            handler=lambda args: {"playbook": "success", "v": args.get("v", 0)},
        )
    )
    runtime = AgentRuntime(tool_registry=registry)
    ctx = RequestContext(
        request_id="playbook-req-ok",
        run_id="playbook-run-ok",
        tenant_id="tx",
        user_id="u1",
        trace_id="tr-ok",
        role="admin",
    )
    result = runtime.run_turn(ctx, "playbook_probe", {"v": 1})
    assert result.status == RuntimeStatus.COMPLETED
    assert audit.query(run_id="playbook-run-ok")


@pytest.mark.integration
def test_playbook_failure_branch_policy_denied_audited(tmp_path: Path) -> None:
    p = tmp_path / "playbook-deny.yaml"
    p.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: deny",
                "default_reason: no rule for this role",
                "rules:",
                "  - id: allow-probe",
                "    decision: allow",
                "    reason: ok",
                "    tools: [playbook_probe]",
                "    roles: [admin]",
            ]
        ),
        encoding="utf-8",
    )
    engine = PolicyEngine.load(p)
    audit = InMemoryAuditSink()
    registry = ToolRegistry(
        policy_engine=engine,
        budget_ledger=BudgetLedger({"tx": TenantBudget(5000, 50.0, 4)}),
        audit_sink=audit,
    )
    registry.register(
        ToolSpec(
            name="playbook_probe",
            capabilities=["utility"],
            risk_level="low",
            handler=lambda _: {"playbook": "should_not_run"},
        )
    )
    runtime = AgentRuntime(tool_registry=registry)
    ctx = RequestContext(
        request_id="playbook-req-deny",
        run_id="playbook-run-deny",
        tenant_id="tx",
        user_id="u1",
        trace_id="tr-deny",
        role="guest",
    )
    result = runtime.run_turn(ctx, "playbook_probe", {})
    assert result.status == RuntimeStatus.BLOCKED
    assert result.error_code == "policy_denied"
    assert audit.query(run_id="playbook-run-deny")
