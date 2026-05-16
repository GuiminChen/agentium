"""Unit tests for ToolRegistry HTTP catalog snapshots."""

from __future__ import annotations

from pathlib import Path

from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.tools.contract import ToolContract
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


def _policy_allow_all(tmp_path: Path) -> Path:
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


def test_list_catalog_entries_sorted_contract_block(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_policy_allow_all(tmp_path))
    registry = ToolRegistry(
        policy_engine=engine,
        budget_ledger=BudgetLedger({"t": TenantBudget(100, 1.0, 4)}),
        audit_sink=InMemoryAuditSink(),
        approval_gate=ApprovalGate(),
    )
    registry.register(
        ToolSpec(name="zebra", capabilities=["z"], risk_level="low", handler=lambda a: {"z": 1})
    )
    registry.register(
        ToolSpec(name="alpha", capabilities=["a"], risk_level="high", handler=lambda a: {"a": 1}),
        contract=ToolContract(
            name="alpha",
            version="v2",
            description="Alpha tool for tests",
            input_schema={"type": "object", "properties": {"x": {"type": "number"}}},
            examples=[{"x": 1}],
        ),
    )

    rows = registry.list_catalog_entries()
    assert [r["name"] for r in rows] == ["alpha", "zebra"]
    alpha = rows[0]
    assert alpha["capabilities"] == ["a"]
    assert alpha["risk_level"] == "high"
    assert alpha["supply_origin"] == "builtin"
    assert alpha["has_contract"] is True
    assert alpha["contract"]["version"] == "v2"
    assert alpha["contract"]["description"] == "Alpha tool for tests"
    assert alpha["contract"]["input_schema"]["type"] == "object"
    zebra = rows[1]
    assert zebra["supply_origin"] == "builtin"
    assert zebra["has_contract"] is False
    assert "contract" not in zebra
