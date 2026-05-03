from __future__ import annotations

from pathlib import Path

import pytest

from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.models.context import RequestContext
from agentium.shared.errors import PolicyDeniedError
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


def _write_policy(tmp_path: Path, version: str, tool_name: str) -> Path:
    path = tmp_path / (version + ".yaml")
    path.write_text(
        "\n".join(
            [
                "version: " + version,
                "default_decision: deny",
                "default_reason: denied by default",
                "rules:",
                "  - id: allow-" + tool_name,
                "    decision: allow",
                "    reason: allowed",
                "    tools: [" + tool_name + "]",
                "    roles: [admin]",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _context(tenant_id: str) -> RequestContext:
    return RequestContext(
        request_id="req-1",
        run_id="run-1",
        tenant_id=tenant_id,
        user_id="user-1",
        trace_id="trace-1",
        role="admin",
        deployment_mode="prod",
    )


def test_tool_registry_uses_policy_selector_by_tenant(tmp_path: Path) -> None:
    default_engine = PolicyEngine.load(_write_policy(tmp_path, "stable-v1", "read_profile"))
    canary_engine = PolicyEngine.load(_write_policy(tmp_path, "candidate-v2", "db_export"))
    audit_sink = InMemoryAuditSink()

    registry = ToolRegistry(
        policy_engine=default_engine,
        policy_selector=lambda context: canary_engine
        if context.tenant_id == "tenant-a"
        else default_engine,
        budget_ledger=BudgetLedger(
            {
                "tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1),
                "tenant-b": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1),
            }
        ),
        audit_sink=audit_sink,
    )
    registry.register(
        ToolSpec(
            name="db_export",
            capabilities=["db.export"],
            risk_level="high",
            handler=lambda args: {"ok": True},
        )
    )

    result = registry.execute(_context("tenant-a"), "db_export", {})
    assert result.output == {"ok": True}

    with pytest.raises(PolicyDeniedError):
        registry.execute(_context("tenant-b"), "db_export", {})

    tenant_a_events = audit_sink.query(run_id="run-1", tenant_id="tenant-a")
    assert any(record.policy_version == "candidate-v2" for record in tenant_a_events)
