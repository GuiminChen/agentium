"""Tests for :mod:`agentium.governance.tool_approval.gate`."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agentium.governance.tool_approval.gate import ToolApprovalDecision, ToolApprovalGate
from tests.unit.app.test_identity_factory import _minimal_app_settings


def test_auto_off_allows(tmp_path: Path) -> None:
    base = _minimal_app_settings(tmp_path)
    gate = ToolApprovalGate(replace(base, tool_approval_auto_enabled=False))
    d = gate.evaluate(
        user_message_excerpt="hi",
        tool_name="any",
        arguments={},
        tool_allowlist=None,
        trace_id="t",
        request_id="r",
    )
    assert d.verdict == "allow"
    assert d.reason_code == "approval_auto_disabled"


def test_allowlist_tier1(tmp_path: Path) -> None:
    base = _minimal_app_settings(tmp_path)
    gate = ToolApprovalGate(replace(base, tool_approval_auto_enabled=True))
    d = gate.evaluate(
        user_message_excerpt="hi",
        tool_name="alpha",
        arguments={},
        tool_allowlist=["alpha"],
        trace_id="t",
        request_id="r",
    )
    assert d.verdict == "allow"
    assert d.reason_code == "tier1_allowlist"


def test_rule_deny(tmp_path: Path) -> None:
    base = _minimal_app_settings(tmp_path)
    gate = ToolApprovalGate(replace(base, tool_approval_auto_enabled=True))
    d = gate.evaluate(
        user_message_excerpt="hi",
        tool_name="noop",
        arguments={"cmd": "rm -rf /"},
        tool_allowlist=None,
        trace_id="t",
        request_id="r",
    )
    assert d.verdict == "deny"
    assert d.reason_code == "tier1_rule_deny"


def test_classifier_fn_injected(tmp_path: Path) -> None:
    base = _minimal_app_settings(tmp_path)

    def _fn(**kwargs: object) -> ToolApprovalDecision:
        del kwargs
        return ToolApprovalDecision("allow", "mock_ok", classifier_stage="test")

    gate = ToolApprovalGate(
        replace(base, tool_approval_auto_enabled=True),
        classifier_fn=_fn,
    )
    d = gate.evaluate(
        user_message_excerpt="hi",
        tool_name="unknown",
        arguments={},
        tool_allowlist=None,
        trace_id="t",
        request_id="r",
    )
    assert d.verdict == "allow"
    assert d.reason_code == "mock_ok"
