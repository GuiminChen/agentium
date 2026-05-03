from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib import error, request

from agentium.api.control_plane import ControlPlaneAPI
from agentium.api.http_control_plane import build_http_server
from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.core.agent_runtime import AgentRuntime
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.governance.policy_release import HMACPolicySigner
from agentium.governance.policy_release_manager import PolicyReleaseManager
from agentium.tools.tool_registry import ToolRegistry


def _write_policy(tmp_path: Path) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: deny",
                "default_reason: denied by default",
                "rules: []",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _policy_document(version: str) -> Dict[str, Any]:
    return {
        "version": version,
        "default_decision": "deny",
        "default_reason": "denied by default",
        "rules": [],
    }


def _build_api(tmp_path: Path, signer: HMACPolicySigner) -> ControlPlaneAPI:
    audit_sink = InMemoryAuditSink()
    policy_release_manager = PolicyReleaseManager(signer=signer, audit_sink=audit_sink)
    registry = ToolRegistry(
        policy_engine=PolicyEngine.load(_write_policy(tmp_path)),
        budget_ledger=BudgetLedger(
            {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
        ),
        audit_sink=audit_sink,
    )
    runtime = AgentRuntime(tool_registry=registry)
    return ControlPlaneAPI(
        runtime=runtime,
        approval_service=ApprovalGate(),
        audit_sink=audit_sink,
        policy_release_manager=policy_release_manager,
    )


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
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_http_policy_release_lifecycle(tmp_path: Path) -> None:
    signer = HMACPolicySigner(secret="dev-secret")
    api = _build_api(tmp_path, signer)
    server = build_http_server(api=api, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    headers = {"X-Tenant-Id": "tenant-a", "X-User-Id": "release-admin", "X-Role": "admin"}
    document = _policy_document("candidate-v1")
    try:
        status, body = _http_json(
            "POST",
            base + "/v1/policies/releases",
            headers=headers,
            payload={
                "run_id": "run-release-1",
                "request_id": "req-release-1",
                "trace_id": "trace-release-1",
                "bundle": {
                    "version": "candidate-v1",
                    "policy_document": document,
                    "signature": signer.sign("candidate-v1", document),
                    "metadata": {"submitted_by": "release-admin"},
                },
            },
        )
        assert status == 202
        release_id = body["release_id"]
        assert body["status"] == "pending_approval"

        status, body = _http_json(
            "POST",
            base + "/v1/policies/releases/" + release_id + "/approve",
            payload={"approver_id": "security-1", "comment": "approved"},
        )
        assert status == 200
        assert body["status"] == "approved"

        status, body = _http_json(
            "POST",
            base + "/v1/policies/releases/" + release_id + "/activate",
            payload={"tenant_ids": ["tenant-a"], "activated_by": "release-admin"},
        )
        assert status == 200
        assert body["status"] == "active"

        status, body = _http_json("GET", base + "/v1/policies/releases/" + release_id)
        assert status == 200
        assert body["release_id"] == release_id
        assert body["active_tenants"] == ["tenant-a"]

        status, body = _http_json(
            "POST",
            base + "/v1/policies/releases/" + release_id + "/rollback",
            payload={"rolled_back_by": "ops-1"},
        )
        assert status == 200
        assert body["status"] == "rolled_back"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_policy_release_rejects_bad_signature(tmp_path: Path) -> None:
    signer = HMACPolicySigner(secret="dev-secret")
    api = _build_api(tmp_path, signer)
    server = build_http_server(api=api, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    headers = {"X-Tenant-Id": "tenant-a", "X-User-Id": "release-admin", "X-Role": "admin"}
    try:
        status, body = _http_json(
            "POST",
            base + "/v1/policies/releases",
            headers=headers,
            payload={
                "run_id": "run-release-2",
                "request_id": "req-release-2",
                "trace_id": "trace-release-2",
                "bundle": {
                    "version": "candidate-v1",
                    "policy_document": _policy_document("candidate-v1"),
                    "signature": "bad-signature",
                    "metadata": {},
                },
            },
        )
        assert status == 403
        assert body["error"] == "policy_release_denied"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
