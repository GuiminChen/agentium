"""Built-in tool registration for bootstrap."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.app.settings import load_settings
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.policy_engine import PolicyEngine
from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.tools.builtin_defaults import builtin_tool_specs_for_profile, register_builtin_tools
from agentium.tools.tool_registry import ToolRegistry


def test_prod_builtin_subset_smaller_than_dev() -> None:
    prod = builtin_tool_specs_for_profile("prod")
    dev = builtin_tool_specs_for_profile("dev")
    assert len(prod) >= 5
    assert len(dev) >= 20
    assert len(prod) < len(dev)
    prod_names = {s.name for s in prod}
    assert "echo_tool" in prod_names
    assert "db_export" in prod_names
    dev_names = {s.name for s in dev}
    assert prod_names <= dev_names


def test_register_builtin_tools_dev_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "version: t\ndefault_decision: deny\ndefault_reason: x\nrules: []\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_POLICY_PATH", str(policy))
    monkeypatch.setenv("AGENTIUM_PROFILE", "dev")
    settings = load_settings()
    pe = PolicyEngine.load(policy)
    ledger = BudgetLedger({}, default_budget=TenantBudget(1000, 10.0, 8))
    reg = ToolRegistry(
        policy_engine=pe,
        budget_ledger=ledger,
        audit_sink=InMemoryAuditSink(),
        approval_gate=ApprovalGate(),
    )
    register_builtin_tools(reg, settings)
    assert len(reg.list_catalog_entries()) >= 20

