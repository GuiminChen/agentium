"""HTTP evolution trajectory ingestion (``POST /v1/evolution/trajectory``)."""

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
from agentium.app.plugins_config import EvolutionPluginConfigSection
from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.core.agent_runtime import AgentRuntime
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.evolution_plugin import NativeEvolutionPlugin, build_evolution_plugin
from agentium.governance.policy_engine import PolicyEngine
from agentium.governance.proposal_queue import ProposalQueue
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


def _write_policy(tmp_path: Path) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: deny",
                "default_reason: denied by default",
                "rules:",
                "  - id: allow-echo",
                "    decision: allow",
                "    reason: allow",
                "    tools: [echo_tool]",
                "    roles: [admin, user]",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _api(tmp_path: Path) -> ControlPlaneAPI:
    policy_engine = PolicyEngine.load(_write_policy(tmp_path))
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
            handler=lambda args: {"ok": True},
        )
    )
    runtime = AgentRuntime(tool_registry=registry)
    return ControlPlaneAPI(runtime=runtime, approval_service=gate, audit_sink=audit_sink)


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
    req.add_header("Content-Type", "application/json")
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)
    try:
        with request.urlopen(req, timeout=5) as resp:
            status = resp.getcode()
            body = json.loads(resp.read().decode("utf-8"))
            return status, body
    except error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        return exc.code, body


def test_evolution_trajectory_http_disabled_returns_404(tmp_path: Path) -> None:
    api = _api(tmp_path)
    resrc = HTTPControlPlaneResources(
        evolution_http_enabled=False,
        evolution_plugin=NativeEvolutionPlugin(),
    )
    server = build_http_server(api=api, host="127.0.0.1", port=0, resources=resrc)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    hdrs = {"X-Tenant-Id": "tenant-a", "X-User-Id": "u1", "X-Role": "admin"}
    try:
        st, body = _http_json(
            "POST",
            base + "/v1/evolution/trajectory",
            headers=hdrs,
            payload={
                "run_id": "r1",
                "request_id": "rq1",
                "trace_id": "t1",
                "events": [{"step_type": "alpha ping", "payload": {}}],
            },
        )
        assert st == 404
        assert body["error"] == "evolution_http_disabled"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_evolution_trajectory_http_user_role_forbidden(tmp_path: Path) -> None:
    api = _api(tmp_path)
    resrc = HTTPControlPlaneResources(
        evolution_http_enabled=True,
        evolution_plugin=NativeEvolutionPlugin(),
    )
    server = build_http_server(api=api, host="127.0.0.1", port=0, resources=resrc)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    hdrs = {"X-Tenant-Id": "tenant-a", "X-User-Id": "u1", "X-Role": "user"}
    try:
        st, body = _http_json(
            "POST",
            base + "/v1/evolution/trajectory",
            headers=hdrs,
            payload={
                "run_id": "r1",
                "request_id": "rq1",
                "trace_id": "t1",
                "events": [{"step_type": "u.step", "payload": {}}],
            },
        )
        assert st == 403
        assert body["error"] == "forbidden"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_evolution_trajectory_http_admin_creates_proposals(tmp_path: Path) -> None:
    api = _api(tmp_path)
    pq = ProposalQueue(audit_sink=None)
    section = EvolutionPluginConfigSection(
        plugin="hermes_class",
        http_enabled=True,
    )
    plugin = build_evolution_plugin(section, pq, audit_sink=None)
    resrc = HTTPControlPlaneResources(
        evolution_http_enabled=True,
        evolution_plugin=plugin,
    )
    server = build_http_server(api=api, host="127.0.0.1", port=0, resources=resrc)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    hdrs = {"X-Tenant-Id": "tenant-a", "X-User-Id": "u1", "X-Role": "admin"}
    try:
        st, body = _http_json(
            "POST",
            base + "/v1/evolution/trajectory",
            headers=hdrs,
            payload={
                "run_id": "r-ev-1",
                "request_id": "rq-ev-1",
                "trace_id": "tr-ev-1",
                "events": [
                    {"step_type": "operator.feedback", "payload": {"note": "tune prompt"}},
                ],
            },
        )
        assert st == 200
        assert body["accepted"] is True
        assert body["event_count"] == 1
        pending = pq.list_pending(tenant_id="tenant-a")
        assert len(pending) >= 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
