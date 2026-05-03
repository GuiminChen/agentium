"""Signature gate for MCP-style plugin descriptors."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.plugins.mcp_loader import McpLoader, McpToolDescriptor, McpUnsignedPluginError
from agentium.tools.contract import ToolContract
from agentium.tools.tool_registry import ToolRegistry


def _policy(tmp_path: Path) -> PolicyEngine:
    p = tmp_path / "p.yaml"
    p.write_text(
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
    return PolicyEngine.load(p)


def _contract(name: str) -> ToolContract:
    return ToolContract(
        name=name,
        description="test mcp tool",
        examples=[{"x": 1}],
    )


def test_mcp_loader_blocks_unsigned_when_required(tmp_path: Path) -> None:
    sink = InMemoryAuditSink()
    registry = ToolRegistry(
        policy_engine=_policy(tmp_path),
        budget_ledger=BudgetLedger({"t": TenantBudget(100, 1.0, 4)}),
        audit_sink=sink,
        approval_gate=ApprovalGate(),
    )
    loader = McpLoader(registry, require_signature=True, audit_sink=sink)
    desc = McpToolDescriptor(
        name="ext_tool",
        capabilities=["echo"],
        risk_level="low",
        handler=lambda a: a,
        contract=_contract("ext_tool"),
    )
    with pytest.raises(McpUnsignedPluginError):
        loader.register_descriptor(desc)
    assert any(e.event_type == "mcp_plugin_unsigned_blocked" for e in sink.query())


def test_mcp_loader_accepts_digest_when_required(tmp_path: Path) -> None:
    registry = ToolRegistry(
        policy_engine=_policy(tmp_path),
        budget_ledger=BudgetLedger({"t": TenantBudget(100, 1.0, 4)}),
        audit_sink=InMemoryAuditSink(),
        approval_gate=ApprovalGate(),
    )
    loader = McpLoader(registry, require_signature=True)
    desc = McpToolDescriptor(
        name="signed_ext",
        capabilities=["echo"],
        risk_level="low",
        handler=lambda a: {"ok": True},
        contract=_contract("signed_ext"),
        signature_digest="sha256:deadbeef",
    )
    loader.register_descriptor(desc)
    names = {e["name"] for e in registry.list_catalog_entries()}
    assert "signed_ext" in names
