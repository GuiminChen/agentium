"""Dev-only GET /v1/dev/lsp-capabilities."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib import error, request

import pytest

from agentium.api.control_plane import ControlPlaneAPI
from agentium.api.http.resources import HTTPControlPlaneResources
from agentium.api.http_control_plane import build_http_server
from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.core.agent_runtime import AgentRuntime
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


def _policy(tmp_path: Path) -> Path:
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
    return p


def _http_json(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Dict[str, Any]]:
    req = request.Request(url, method=method)
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


@pytest.mark.integration
def test_dev_lsp_capabilities_404_when_disabled(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_policy(tmp_path))
    ledger = BudgetLedger(
        {"t1": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
    )
    audit = InMemoryAuditSink()
    gate = ApprovalGate()
    registry = ToolRegistry(
        policy_engine=engine, budget_ledger=ledger, audit_sink=audit, approval_gate=gate
    )
    registry.register(
        ToolSpec(name="noop", capabilities=[], risk_level="low", handler=lambda a: {})
    )
    runtime = AgentRuntime(tool_registry=registry)
    api = ControlPlaneAPI(runtime=runtime, approval_service=gate, audit_sink=audit)
    resources = HTTPControlPlaneResources(dev_http_enabled=False, lsp_upstream_configured=False)
    server = build_http_server(api=api, host="127.0.0.1", port=0, resources=resources)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    headers = {"X-Tenant-Id": "t1", "X-User-Id": "u1", "X-Role": "admin"}
    try:
        st, body = _http_json("GET", base + "/v1/dev/lsp-capabilities", headers=headers)
        assert st == 404
        assert body.get("error") == "endpoint_not_found"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.integration
def test_dev_lsp_capabilities_ok_when_enabled_reflects_upstream_flag(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_policy(tmp_path))
    ledger = BudgetLedger(
        {"t1": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
    )
    audit = InMemoryAuditSink()
    gate = ApprovalGate()
    registry = ToolRegistry(
        policy_engine=engine, budget_ledger=ledger, audit_sink=audit, approval_gate=gate
    )
    registry.register(
        ToolSpec(name="noop", capabilities=[], risk_level="low", handler=lambda a: {})
    )
    runtime = AgentRuntime(tool_registry=registry)
    api = ControlPlaneAPI(runtime=runtime, approval_service=gate, audit_sink=audit)
    resources = HTTPControlPlaneResources(dev_http_enabled=True, lsp_upstream_configured=True)
    server = build_http_server(api=api, host="127.0.0.1", port=0, resources=resources)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    headers = {"X-Tenant-Id": "t1", "X-User-Id": "u1", "X-Role": "admin"}
    try:
        st, body = _http_json("GET", base + "/v1/dev/lsp-capabilities", headers=headers)
        assert st == 200
        assert body.get("lsp_upstream_configured") is True
        assert body.get("websocket_proxy_available") is False
        assert "rfc_path" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
