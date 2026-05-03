"""Integration: GET /v1/tools lists registered tools (echo_tool) on live HTTP server."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib import error, request

import pytest

from agentium.api.control_plane import ControlPlaneAPI
from agentium.api.http_control_plane import build_http_server
from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.core.agent_runtime import AgentRuntime
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


def _write_policy_allow_admin_tools(tmp_path: Path) -> Path:
    path = tmp_path / "policy_tools_catalog.yaml"
    path.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: deny",
                "default_reason: denied by default",
                "rules:",
                "  - id: allow-tools",
                "    decision: allow",
                "    reason: admin tools",
                "    tools: [db_export, echo_tool]",
                "    roles: [admin]",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _http_json(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Dict[str, Any]]:
    encoded = b""
    if payload is not None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=encoded if payload is not None else None, method=method)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)
    try:
        with request.urlopen(req, timeout=15) as resp:
            status = resp.getcode()
            body = json.loads(resp.read().decode("utf-8"))
            return status, body
    except error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        return exc.code, body


def _build_api_with_echo(tmp_path: Path) -> ControlPlaneAPI:
    policy_engine = PolicyEngine.load(_write_policy_allow_admin_tools(tmp_path))
    ledger = BudgetLedger(
        {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
    )
    gate = ApprovalGate()
    audit_sink = InMemoryAuditSink()
    registry = ToolRegistry(
        policy_engine=policy_engine,
        budget_ledger=ledger,
        audit_sink=audit_sink,
        approval_gate=gate,
    )
    registry.register(
        ToolSpec(
            name="echo_tool",
            capabilities=["echo"],
            risk_level="low",
            handler=lambda args: {"echo": args.get("text", "")},
        )
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
    return ControlPlaneAPI(runtime=runtime, approval_service=gate, audit_sink=audit_sink)


@pytest.mark.integration
def test_integration_http_get_tools_includes_echo_tool(tmp_path: Path) -> None:
    api = _build_api_with_echo(tmp_path)
    server = build_http_server(api=api, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    headers = {
        "X-Tenant-Id": "tenant-a",
        "X-User-Id": "user-1",
        "X-Role": "admin",
    }
    try:
        st, body = _http_json(method="GET", url=base + "/v1/tools", headers=headers)
        assert st == 200
        names = {t["name"] for t in body["tools"]}
        assert "echo_tool" in names
        assert body["count"] >= 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.integration
def test_integration_http_get_tools_forbidden_for_guest_role(tmp_path: Path) -> None:
    api = _build_api_with_echo(tmp_path)
    server = build_http_server(api=api, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    headers = {
        "X-Tenant-Id": "tenant-a",
        "X-User-Id": "user-1",
        "X-Role": "guest",
    }
    try:
        st, body = _http_json(method="GET", url=base + "/v1/tools", headers=headers)
        assert st == 403
        assert body.get("error") == "forbidden"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
