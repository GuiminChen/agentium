from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.integrations.connector_registry import ConnectorRegistry
from agentium.models.context import RequestContext
from agentium.shared.errors import ConfigurationError
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


class _DummyConnector:
    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": True, "request": request}


def _allow_policy(tmp_path: Path) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(
        "\n".join(
            [
                "version: p-b5",
                "default_decision: deny",
                "default_reason: denied by default",
                "rules:",
                "  - id: allow-connector-tool",
                "    decision: allow",
                "    reason: connector tool allowed",
                "    tools: [http_sync]",
                "    roles: [admin]",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _context() -> RequestContext:
    return RequestContext(
        request_id="req-1",
        run_id="run-1",
        tenant_id="tenant-a",
        user_id="user-1",
        trace_id="trace-1",
        role="admin",
        deployment_mode="prod",
    )


def test_connector_registry_wraps_connector_as_tool_handler() -> None:
    registry = ConnectorRegistry()
    registry.register("std_http", _DummyConnector())

    handler = registry.as_tool_handler("std_http", default_operation="sync")
    result = handler({"params": {"a": 1}, "context": {"tenant_id": "tenant-a"}})

    assert result["ok"] is True
    assert result["request"]["operation"] == "sync"
    assert result["request"]["params"]["a"] == 1


def test_connector_registry_rejects_unknown_connector() -> None:
    registry = ConnectorRegistry()
    with pytest.raises(ConfigurationError):
        registry.as_tool_handler("missing-connector")


def test_connector_tool_runs_through_tool_registry_governance(tmp_path: Path) -> None:
    connector_registry = ConnectorRegistry()
    connector_registry.register("std_http", _DummyConnector())

    audit_sink = InMemoryAuditSink()
    tool_registry = ToolRegistry(
        policy_engine=PolicyEngine.load(_allow_policy(tmp_path)),
        budget_ledger=BudgetLedger(
            {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
        ),
        audit_sink=audit_sink,
    )
    tool_registry.register(
        ToolSpec(
            name="http_sync",
            capabilities=["integration.http"],
            risk_level="medium",
            handler=connector_registry.as_tool_handler("std_http", default_operation="sync"),
        )
    )

    output = tool_registry.execute(
        context=_context(),
        name="http_sync",
        args={"params": {"dataset": "daily"}},
    ).output

    assert output["ok"] is True
    assert output["request"]["params"]["dataset"] == "daily"
    records = audit_sink.query(run_id="run-1", tenant_id="tenant-a")
    assert any(record.event_type == "policy_decision" for record in records)
