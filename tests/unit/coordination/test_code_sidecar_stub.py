"""Code MCP sidecar stub (P1-12)."""

from __future__ import annotations

from agentium.coordination.code_mcp_sidecar import run_python_tool_orchestration_stub


def test_sidecar_stub_records_net_policy() -> None:
    out = run_python_tool_orchestration_stub(
        code="print(1)",
        allowed_mcp_endpoints=("https://mcp.example/tools",),
        net_allow_hosts=frozenset({"api.example.com"}),
    )
    assert out["status"] == "stub_no_code_execution"
    assert "api.example.com" in out["sidecar_net_policy"]["egress_allow_hosts"]


def test_sidecar_stub_egress_denied_when_probe_host_not_allowlisted() -> None:
    out = run_python_tool_orchestration_stub(
        code="import requests",
        allowed_mcp_endpoints=("https://mcp.example/tools",),
        net_allow_hosts=frozenset({"api.example.com"}),
        egress_probe_url="https://evil.example/fetch",
    )
    assert out["status"] == "egress_denied"
    assert out["egress_probe_url"] == "https://evil.example/fetch"


def test_sidecar_stub_egress_allowed_when_probe_host_matches() -> None:
    out = run_python_tool_orchestration_stub(
        code="pass",
        net_allow_hosts=frozenset({"api.example.com"}),
        egress_probe_url="https://api.example.com/ok",
    )
    assert out["status"] == "stub_no_code_execution"
