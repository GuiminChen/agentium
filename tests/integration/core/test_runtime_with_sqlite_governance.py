from __future__ import annotations

from pathlib import Path

import pytest

from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.core.agent_runtime import AgentRuntime, RuntimeStatus
from agentium.governance.policy_engine import PolicyEngine
from agentium.infra.db.sqlite_store import SqliteApprovalGate, SqliteAuditSink
from agentium.models.context import RequestContext
from agentium.shared.errors import ApprovalRequiredError
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


def _write_policy(tmp_path: Path) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: deny",
                "default_reason: denied by default",
                "rules:",
                "  - id: require-db-approval",
                "    decision: require_approval",
                "    reason: export requires approval",
                "    tools: [db_export]",
                "    roles: [admin]",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_tool_registry_with_sqlite_approval_and_audit(tmp_path: Path) -> None:
    policy_engine = PolicyEngine.load(_write_policy(tmp_path))
    ledger = BudgetLedger(
        {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
    )
    db_path = tmp_path / "runtime" / "agentium.db"
    audit = SqliteAuditSink(db_path)
    gate = SqliteApprovalGate(db_path)
    registry = ToolRegistry(
        policy_engine=policy_engine,
        budget_ledger=ledger,
        audit_sink=audit,
        approval_gate=gate,
    )
    registry.register(
        ToolSpec(
            name="db_export",
            capabilities=["db.export"],
            risk_level="high",
            handler=lambda args: {"ok": True, "dataset": args["dataset"]},
        )
    )
    context = RequestContext(
        request_id="req-1",
        run_id="run-1",
        tenant_id="tenant-a",
        user_id="user-1",
        trace_id="trace-1",
        role="admin",
        deployment_mode="prod",
    )

    with pytest.raises(ApprovalRequiredError) as exc:
        registry.execute(context=context, name="db_export", args={"dataset": "daily"})
    approval_id = exc.value.approval_id
    assert approval_id is not None

    assert gate.approve(approval_id, approver_id="reviewer-1") is True
    resumed = registry.execute_after_approval(
        context=context,
        name="db_export",
        approval_id=approval_id,
        args={"dataset": "daily"},
    )
    events = audit.query(run_id="run-1")
    audit.close()
    gate.close()

    assert resumed.output["ok"] is True
    assert any(event.event_type == "approval_requested" for event in events)
    assert any(event.event_type == "tool_executed" for event in events)


def test_runtime_pending_and_resume_with_sqlite(tmp_path: Path) -> None:
    policy_engine = PolicyEngine.load(_write_policy(tmp_path))
    ledger = BudgetLedger(
        {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
    )
    db_path = tmp_path / "runtime" / "agentium.db"
    audit = SqliteAuditSink(db_path)
    gate = SqliteApprovalGate(db_path)
    registry = ToolRegistry(
        policy_engine=policy_engine,
        budget_ledger=ledger,
        audit_sink=audit,
        approval_gate=gate,
    )
    registry.register(
        ToolSpec(
            name="db_export",
            capabilities=["db.export"],
            risk_level="high",
            handler=lambda args: {"ok": True, "dataset": args["dataset"]},
        )
    )
    runtime = AgentRuntime(tool_registry=registry)
    context = RequestContext(
        request_id="req-2",
        run_id="run-2",
        tenant_id="tenant-a",
        user_id="user-2",
        trace_id="trace-2",
        role="admin",
        deployment_mode="prod",
    )

    pending = runtime.run_turn(context=context, tool_name="db_export", args={"dataset": "daily"})
    assert pending.status == RuntimeStatus.PENDING_APPROVAL
    assert pending.approval_id is not None

    assert gate.approve(pending.approval_id, approver_id="reviewer-2") is True
    resumed = runtime.resume_turn(
        context=context,
        tool_name="db_export",
        approval_id=pending.approval_id,
        args={"dataset": "daily"},
    )
    audit.close()
    gate.close()

    assert resumed.status == RuntimeStatus.COMPLETED
    assert resumed.output == {"ok": True, "dataset": "daily"}
