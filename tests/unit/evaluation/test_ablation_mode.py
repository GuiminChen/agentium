"""Unit tests for evaluation-only ablation mode switches."""

from __future__ import annotations

import pytest

from agentium.evaluation.ablation_mode import (
    ablation_variant,
    bypass_manifest_allowlist,
    coerce_policy_allow,
    evaluation_ablation_enabled,
    effective_variant,
)
from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.models.context import RequestContext
from agentium.shared.errors import PolicyDeniedError
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


def test_evaluation_ablation_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTIUM_EVALUATION_ABLATION", raising=False)
    monkeypatch.delenv("AGENTIUM_ABLATION_VARIANT", raising=False)
    assert evaluation_ablation_enabled() is False
    assert effective_variant() is None
    assert bypass_manifest_allowlist() is False
    assert coerce_policy_allow() is False


def test_variant_no_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("AGENTIUM_EVALUATION_ABLATION", "1")
    monkeypatch.setenv("AGENTIUM_ABLATION_VARIANT", "no_manifest")
    assert ablation_variant() == "no_manifest"
    assert bypass_manifest_allowlist() is True


def test_variant_permissive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTIUM_EVALUATION_ABLATION", "1")
    monkeypatch.setenv("AGENTIUM_ABLATION_VARIANT", "permissive")
    assert ablation_variant() == "permissive"
    assert coerce_policy_allow() is True


def test_manifest_bypass_exec(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """no_manifest skips allowlist denial for evaluation harness only."""

    p = tmp_path / "pol.yaml"
    p.write_text(
        "\n".join(
            [
                "version: p-ab",
                "default_decision: deny",
                "default_reason: denied",
                "rules:",
                "  - id: allow-sum",
                "    decision: allow",
                "    reason: ok",
                "    tools: [sum_numbers]",
                "    roles: [analyst]",
            ]
        ),
        encoding="utf-8",
    )
    engine = PolicyEngine.load(p)
    ledger = BudgetLedger(
        {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
    )
    audit = InMemoryAuditSink()
    reg = ToolRegistry(policy_engine=engine, budget_ledger=ledger, audit_sink=audit)
    reg.register(
        ToolSpec(
            name="sum_numbers",
            capabilities=["math"],
            risk_level="low",
            handler=lambda args: {"value": int(args["a"]) + int(args["b"])},
        )
    )
    ctx = RequestContext(
        request_id="r1",
        run_id="run-ab",
        tenant_id="tenant-a",
        user_id="user-1",
        trace_id="t1",
        role="analyst",
        manifest_declared_tools=["other_tool"],
        run_manifest_sha256="abcd" * 16,
    )
    monkeypatch.setenv("AGENTIUM_EVALUATION_ABLATION", "1")
    monkeypatch.setenv("AGENTIUM_ABLATION_VARIANT", "no_manifest")
    out = reg.execute(ctx, "sum_numbers", {"a": 2, "b": 3})
    assert out.output["value"] == 5


def test_permissive_coerces_allow(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """permissive allows calls that would otherwise be denied by rules."""

    p = tmp_path / "pol.yaml"
    p.write_text(
        "\n".join(
            [
                "version: p-z",
                "default_decision: deny",
                "default_reason: denied",
                "rules:",
                "  - id: block-exec",
                "    decision: deny",
                "    reason: blocked",
                "    tools: [bad_tool]",
                "    roles: [analyst]",
            ]
        ),
        encoding="utf-8",
    )
    engine = PolicyEngine.load(p)
    ledger = BudgetLedger({"t": TenantBudget(1000, 10.0, 1)})
    reg = ToolRegistry(
        policy_engine=engine, budget_ledger=ledger, audit_sink=InMemoryAuditSink()
    )
    reg.register(
        ToolSpec(name="bad_tool", capabilities=[], risk_level="high", handler=lambda a: {"x": 1})
    )
    ctx = RequestContext(
        request_id="r1", run_id="run-z", tenant_id="t", user_id="u", trace_id="tr", role="analyst"
    )
    with pytest.raises(PolicyDeniedError):
        reg.execute(ctx, "bad_tool", {})

    monkeypatch.setenv("AGENTIUM_EVALUATION_ABLATION", "1")
    monkeypatch.setenv("AGENTIUM_ABLATION_VARIANT", "permissive")
    out = reg.execute(ctx, "bad_tool", {})
    assert out.output == {"x": 1}
