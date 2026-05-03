from __future__ import annotations

from pathlib import Path

import pytest

from agentium.coordination.budget_ledger import (
    BudgetLedger,
    ResourceLimitController,
    TenantBudget,
)
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.models.context import RequestContext
from agentium.shared.errors import ApprovalRequiredError, BudgetExceededError, PolicyDeniedError
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


def _context(role: str = "analyst", run_id: str = "run-1") -> RequestContext:
    return RequestContext(
        request_id="req-1",
        run_id=run_id,
        tenant_id="tenant-a",
        user_id="user-1",
        trace_id="trace-1",
        role=role,
        deployment_mode="prod",
    )


def _write_policy(tmp_path: Path) -> Path:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: deny",
                "default_reason: denied by default",
                "rules:",
                "  - id: allow-sum",
                "    decision: allow",
                "    reason: sum tool allowed",
                "    tools: [sum_numbers]",
                "    roles: [analyst]",
                "  - id: require-approval-export",
                "    decision: require_approval",
                "    reason: export requires approval",
                "    tools: [db_export]",
                "    roles: [admin]",
            ]
        ),
        encoding="utf-8",
    )
    return policy_path


@pytest.mark.paper
def test_paper_hypothesis_h1_manifest_allowlist_blocks_undeclared_tool(tmp_path: Path) -> None:
    """H1: when manifest_declared_tools is set, tools not in the list are denied (100%)."""

    engine = PolicyEngine.load(_write_policy(tmp_path))
    ledger = BudgetLedger(
        {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
    )
    audit = InMemoryAuditSink()
    registry = ToolRegistry(policy_engine=engine, budget_ledger=ledger, audit_sink=audit)
    registry.register(
        ToolSpec(
            name="sum_numbers",
            capabilities=["math"],
            risk_level="low",
            handler=lambda args: {"value": int(args["a"]) + int(args["b"])},
        )
    )

    ctx = _context()
    ctx2 = ctx.model_copy(
        update={
            "manifest_declared_tools": ["other_tool"],
            "run_manifest_sha256": "deadbeef" * 8,
        }
    )
    with pytest.raises(PolicyDeniedError) as exc:
        registry.execute(ctx2, "sum_numbers", {"a": 1, "b": 2})
    assert "allowlist" in str(exc.value).lower() or "declared" in str(exc.value).lower()
    denied = [e for e in audit.query(run_id="run-1") if e.event_type == "run_manifest_tool_denied"]
    assert len(denied) == 1
    assert denied[0].payload.get("tool_name") == "sum_numbers"


def test_tool_registry_executes_allowed_tool(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_write_policy(tmp_path))
    ledger = BudgetLedger(
        {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
    )
    audit = InMemoryAuditSink()
    registry = ToolRegistry(policy_engine=engine, budget_ledger=ledger, audit_sink=audit)
    registry.register(
        ToolSpec(
            name="sum_numbers",
            capabilities=["math"],
            risk_level="low",
            handler=lambda args: {"value": int(args["a"]) + int(args["b"])},
        )
    )

    result = registry.execute(_context(), "sum_numbers", {"a": 2, "b": 3})

    assert result.output["value"] == 5
    assert result.call_record.status == "success"
    assert len(audit.query(run_id="run-1")) == 2


def test_tool_registry_rejects_hard_resource_limit(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_write_policy(tmp_path))
    ledger = BudgetLedger(
        {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
    )
    registry = ToolRegistry(
        policy_engine=engine,
        budget_ledger=ledger,
        audit_sink=InMemoryAuditSink(),
        resource_controller=ResourceLimitController(
            {
                "tenant-a": TenantBudget(
                    token_limit=1000,
                    cost_limit=10.0,
                    max_concurrency=1,
                    hard_memory_mb=128,
                )
            }
        ),
    )
    registry.register(
        ToolSpec(
            name="sum_numbers",
            capabilities=["math"],
            risk_level="low",
            handler=lambda args: {"value": int(args["a"]) + int(args["b"])},
        )
    )

    with pytest.raises(BudgetExceededError):
        registry.execute(
            _context(),
            "sum_numbers",
            {"a": 2, "b": 3, "resource_demand": {"memory_mb": 256}},
        )


def test_tool_registry_rejects_denied_tool(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_write_policy(tmp_path))
    ledger = BudgetLedger(
        {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
    )
    registry = ToolRegistry(
        policy_engine=engine, budget_ledger=ledger, audit_sink=InMemoryAuditSink()
    )
    registry.register(
        ToolSpec(
            name="delete_data",
            capabilities=["dangerous"],
            risk_level="high",
            handler=lambda args: {"ok": True},
        )
    )

    with pytest.raises(PolicyDeniedError):
        registry.execute(_context(), "delete_data", {})


def test_tool_registry_requires_approval(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_write_policy(tmp_path))
    ledger = BudgetLedger(
        {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
    )
    registry = ToolRegistry(
        policy_engine=engine, budget_ledger=ledger, audit_sink=InMemoryAuditSink()
    )
    registry.register(
        ToolSpec(
            name="db_export",
            capabilities=["db.export"],
            risk_level="high",
            handler=lambda args: {"ok": True},
        )
    )

    with pytest.raises(ApprovalRequiredError):
        registry.execute(_context(role="admin"), "db_export", {})


def test_tool_registry_resume_after_approval(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_write_policy(tmp_path))
    ledger = BudgetLedger(
        {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
    )
    gate = ApprovalGate()
    audit = InMemoryAuditSink()
    registry = ToolRegistry(
        policy_engine=engine,
        budget_ledger=ledger,
        audit_sink=audit,
        approval_gate=gate,
    )
    registry.register(
        ToolSpec(
            name="db_export",
            capabilities=["db.export"],
            risk_level="high",
            handler=lambda args: {"status": "exported"},
        )
    )
    context = _context(role="admin")

    with pytest.raises(ApprovalRequiredError) as exc_info:
        registry.execute(context, "db_export", {"dataset": "daily"})
    approval_id = exc_info.value.approval_id
    assert approval_id is not None
    approved = gate.approve(approval_id, approver_id="reviewer-1")
    assert approved is True

    result = registry.execute_after_approval(
        context=context,
        name="db_export",
        approval_id=approval_id,
        args={"dataset": "daily"},
    )

    assert result.output["status"] == "exported"
    assert len(audit.query(run_id=context.run_id)) >= 4


def test_tool_registry_rejects_after_approval_rejection(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_write_policy(tmp_path))
    ledger = BudgetLedger(
        {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
    )
    gate = ApprovalGate()
    registry = ToolRegistry(
        policy_engine=engine,
        budget_ledger=ledger,
        audit_sink=InMemoryAuditSink(),
        approval_gate=gate,
    )
    registry.register(
        ToolSpec(
            name="db_export",
            capabilities=["db.export"],
            risk_level="high",
            handler=lambda args: {"status": "exported"},
        )
    )
    context = _context(role="admin")

    with pytest.raises(ApprovalRequiredError) as exc_info:
        registry.execute(context, "db_export", {"dataset": "daily"})
    approval_id = exc_info.value.approval_id
    assert approval_id is not None
    rejected = gate.reject(approval_id, approver_id="reviewer-1", comment="not allowed")
    assert rejected is True

    with pytest.raises(PolicyDeniedError):
        registry.execute_after_approval(
            context=context,
            name="db_export",
            approval_id=approval_id,
            args={"dataset": "daily"},
        )


def test_tool_registry_fails_when_budget_rejected(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_write_policy(tmp_path))
    ledger = BudgetLedger(
        {"tenant-a": TenantBudget(token_limit=0, cost_limit=0.0, max_concurrency=1)}
    )
    registry = ToolRegistry(
        policy_engine=engine, budget_ledger=ledger, audit_sink=InMemoryAuditSink()
    )
    registry.register(
        ToolSpec(
            name="sum_numbers",
            capabilities=["math"],
            risk_level="low",
            handler=lambda args: {"value": 1},
        )
    )

    with pytest.raises(BudgetExceededError):
        registry.execute(_context(), "sum_numbers", {"a": 1, "b": 0})
