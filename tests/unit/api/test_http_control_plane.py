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
from agentium.governance.access_control import (
    InsecureJWTDecoder,
    MultiIssuerOIDCIdentityProvider,
    OIDCIdentityProvider,
    Principal,
    StaticTokenIdentityProvider,
)
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
import pytest

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
                "  - id: require-db-approval",
                "    decision: require_approval",
                "    reason: export requires approval",
                "    tools: [db_export]",
                "    roles: [admin]",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _write_policy_allow_admin_tools(tmp_path: Path) -> Path:
    path = tmp_path / "policy_two.yaml"
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


def _build_api(tmp_path: Path) -> ControlPlaneAPI:
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
            name="db_export",
            capabilities=["db.export"],
            risk_level="high",
            handler=lambda args: {"ok": True, "dataset": args["dataset"]},
        )
    )
    runtime = AgentRuntime(tool_registry=registry)
    return ControlPlaneAPI(runtime=runtime, approval_service=gate, audit_sink=audit_sink)


def _build_api_two_tools(tmp_path: Path) -> ControlPlaneAPI:
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


def test_http_control_plane_end_to_end(tmp_path: Path) -> None:
    api = _build_api(tmp_path)
    server = build_http_server(api=api, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    identity_headers = {
        "X-Tenant-Id": "tenant-a",
        "X-User-Id": "user-1",
        "X-Role": "admin",
    }
    try:
        run_status, run_body = _http_json(
            method="POST",
            url=base + "/v1/turn",
            headers=identity_headers,
            payload={
                "tool_name": "db_export",
                "args": {"dataset": "daily"},
                "run_id": "run-1",
                "request_id": "req-1",
                "trace_id": "trace-1",
            },
        )
        assert run_status == 202
        assert run_body["status"] == "pending_approval"
        approval_id = run_body["approval_id"]
        assert approval_id

        approval_status, approval_body = _http_json(
            method="GET",
            url=base + "/v1/approvals/" + approval_id,
        )
        assert approval_status == 200
        assert approval_body["status"] == "pending"

        decision_status, decision_body = _http_json(
            method="POST",
            url=base + "/v1/approvals/" + approval_id + "/decision",
            payload={
                "decision": "approve",
                "approver_id": "reviewer-1",
                "comment": "approved",
            },
        )
        assert decision_status == 200
        assert decision_body["status"] == "approved"

        resume_status, resume_body = _http_json(
            method="POST",
            url=base + "/v1/turns/resume",
            headers=identity_headers,
            payload={
                "tool_name": "db_export",
                "args": {"dataset": "daily"},
                "run_id": "run-1",
                "request_id": "req-2",
                "trace_id": "trace-2",
                "approval_id": approval_id,
            },
        )
        assert resume_status == 200
        assert resume_body["status"] == "completed"
        assert resume_body["output"] == {"ok": True, "dataset": "daily"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_control_plane_rejects_missing_identity_headers(tmp_path: Path) -> None:
    api = _build_api(tmp_path)
    server = build_http_server(api=api, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    try:
        status, body = _http_json(
            method="POST",
            url=base + "/v1/turn",
            payload={
                "tool_name": "db_export",
                "args": {"dataset": "daily"},
                "run_id": "run-1",
                "request_id": "req-1",
                "trace_id": "trace-1",
            },
        )
        assert status == 401
        assert body["error"] == "missing_identity_headers"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_control_plane_accepts_bearer_oidc_identity(tmp_path: Path) -> None:
    api = _build_api(tmp_path)
    identity_provider = StaticTokenIdentityProvider(
        {
            "token-ok": Principal(
                subject="user-oidc-1",
                tenant_id="tenant-a",
                roles={"admin"},
                attributes={"email": "u@example.com"},
            )
        }
    )
    server = build_http_server(
        api=api, host="127.0.0.1", port=0, identity_provider=identity_provider
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    try:
        run_status, run_body = _http_json(
            method="POST",
            url=base + "/v1/turn",
            headers={"Authorization": "Bearer token-ok"},
            payload={
                "tool_name": "db_export",
                "args": {"dataset": "daily"},
                "run_id": "run-oidc-1",
                "request_id": "req-oidc-1",
                "trace_id": "trace-oidc-1",
            },
        )
        assert run_status == 202
        assert run_body["status"] == "pending_approval"
        assert run_body["approval_id"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_bearer_multi_issuer_oidc_resolves_me(tmp_path: Path) -> None:
    import base64 as b64
    import json as js

    def _jwt(iss: str, sub: str, tenant: str) -> str:
        h = b64.urlsafe_b64encode(js.dumps({"alg": "none"}).encode()).decode("utf-8").rstrip("=")
        p = (
            b64.urlsafe_b64encode(
                js.dumps(
                    {
                        "iss": iss,
                        "aud": "api",
                        "sub": sub,
                        "tenant_id": tenant,
                        "roles": ["admin"],
                    }
                ).encode()
            )
            .decode("utf-8")
            .rstrip("=")
        )
        return f"{h}.{p}.x"

    api = _build_api(tmp_path)
    multi = MultiIssuerOIDCIdentityProvider(
        [
            OIDCIdentityProvider(InsecureJWTDecoder(), "https://idp-a.example", "api"),
            OIDCIdentityProvider(InsecureJWTDecoder(), "https://idp-b.example", "api"),
        ]
    )
    server = build_http_server(
        api=api, host="127.0.0.1", port=0, identity_provider=multi, identity_mode="bearer"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    try:
        status, body = _http_json(
            method="GET",
            url=base + "/v1/me",
            headers={"Authorization": "Bearer " + _jwt("https://idp-b.example", "sub-b", "tenant-b")},
        )
        assert status == 200
        assert body["tenant_id"] == "tenant-b"
        assert body["user_id"] == "sub-b"
        st2, bad = _http_json(
            method="GET",
            url=base + "/v1/me",
            headers={
                "Authorization": "Bearer "
                + _jwt("https://idp-unknown.example", "sub-x", "tenant-x")
            },
        )
        assert st2 == 401
        assert bad["error"] == "invalid_bearer_token"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_control_plane_rejects_invalid_bearer_token(tmp_path: Path) -> None:
    api = _build_api(tmp_path)
    identity_provider = StaticTokenIdentityProvider({})
    server = build_http_server(
        api=api, host="127.0.0.1", port=0, identity_provider=identity_provider
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    try:
        status, body = _http_json(
            method="POST",
            url=base + "/v1/turn",
            headers={"Authorization": "Bearer token-bad"},
            payload={
                "tool_name": "db_export",
                "args": {"dataset": "daily"},
                "run_id": "run-oidc-2",
                "request_id": "req-oidc-2",
                "trace_id": "trace-oidc-2",
            },
        )
        assert status == 401
        assert body["error"] == "invalid_bearer_token"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_control_plane_identity_mode_bearer_only(tmp_path: Path) -> None:
    api = _build_api(tmp_path)
    identity_provider = StaticTokenIdentityProvider(
        {
            "token-ok": Principal(
                subject="user-oidc-2",
                tenant_id="tenant-a",
                roles={"admin"},
                attributes={},
            )
        }
    )
    server = build_http_server(
        api=api,
        host="127.0.0.1",
        port=0,
        identity_provider=identity_provider,
        identity_mode="bearer",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    try:
        status, body = _http_json(
            method="POST",
            url=base + "/v1/turn",
            headers={"X-Tenant-Id": "tenant-a", "X-User-Id": "user-1"},
            payload={
                "tool_name": "db_export",
                "args": {"dataset": "daily"},
                "run_id": "run-bearer-mode",
                "request_id": "req-bearer-mode",
                "trace_id": "trace-bearer-mode",
            },
        )
        assert status == 401
        assert body["error"] == "missing_bearer_token"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_control_plane_identity_mode_header_only(tmp_path: Path) -> None:
    api = _build_api(tmp_path)
    identity_provider = StaticTokenIdentityProvider(
        {
            "token-ok": Principal(
                subject="user-oidc-3",
                tenant_id="tenant-a",
                roles={"admin"},
                attributes={},
            )
        }
    )
    server = build_http_server(
        api=api,
        host="127.0.0.1",
        port=0,
        identity_provider=identity_provider,
        identity_mode="header",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    try:
        status, body = _http_json(
            method="POST",
            url=base + "/v1/turn",
            headers={"Authorization": "Bearer token-ok"},
            payload={
                "tool_name": "db_export",
                "args": {"dataset": "daily"},
                "run_id": "run-header-mode",
                "request_id": "req-header-mode",
                "trace_id": "trace-header-mode",
            },
        )
        assert status == 401
        assert body["error"] == "missing_identity_headers"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_control_plane_can_query_audit_events_endpoint(tmp_path: Path) -> None:
    api = _build_api(tmp_path)
    server = build_http_server(api=api, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    headers = {"X-Tenant-Id": "tenant-a", "X-User-Id": "user-1", "X-Role": "admin"}
    try:
        run_status, run_body = _http_json(
            method="POST",
            url=base + "/v1/turn",
            headers=headers,
            payload={
                "tool_name": "db_export",
                "args": {"dataset": "daily"},
                "run_id": "run-audit-1",
                "request_id": "req-audit-1",
                "trace_id": "trace-audit-1",
            },
        )
        assert run_status == 202
        assert run_body["status"] == "pending_approval"

        status, body = _http_json(
            method="GET",
            url=base + "/v1/audit/events?run_id=run-audit-1&event_type=policy_decision&limit=10",
        )
        assert status == 200
        assert body["count"] >= 1
        assert all(event["event_type"] == "policy_decision" for event in body["events"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_control_plane_me_endpoint_header_identity(tmp_path: Path) -> None:
    api = _build_api(tmp_path)
    server = build_http_server(api=api, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    headers = {
        "X-Tenant-Id": "tenant-a",
        "X-User-Id": "user-1",
        "X-Role": "admin",
    }
    try:
        status, body = _http_json(method="GET", url=base + "/v1/me", headers=headers)
        assert status == 200
        assert body["tenant_id"] == "tenant-a"
        assert body["user_id"] == "user-1"
        assert body["role"] == "admin"
        assert body["roles"] == ["admin"]
        assert body["ui_profile"] == "enterprise"
        assert "me.read" in body["capabilities"]
        assert "turn.execute" in body["capabilities"]
        assert "tools.read" in body["capabilities"]
        assert "approval.decide" in body["capabilities"]

        status_alias, body_alias = _http_json(
            method="GET", url=base + "/v1/auth/me", headers=headers
        )
        assert status_alias == 200
        assert body_alias["user_id"] == "user-1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_control_plane_me_endpoint_missing_identity_401(tmp_path: Path) -> None:
    api = _build_api(tmp_path)
    server = build_http_server(api=api, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    try:
        status, body = _http_json(method="GET", url=base + "/v1/me")
        assert status == 401
        assert body["error"] == "missing_identity_headers"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_control_plane_me_endpoint_bearer_multi_role(tmp_path: Path) -> None:
    api = _build_api(tmp_path)
    identity_provider = StaticTokenIdentityProvider(
        {
            "tok": Principal(
                subject="sub-1",
                tenant_id="tenant-a",
                roles={"viewer", "admin"},
                attributes={},
            )
        }
    )
    server = build_http_server(
        api=api, host="127.0.0.1", port=0, identity_provider=identity_provider
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    try:
        status, body = _http_json(
            method="GET",
            url=base + "/v1/me",
            headers={"Authorization": "Bearer tok"},
        )
        assert status == 200
        assert body["roles"] == ["admin", "viewer"]
        assert body["role"] == "admin"
        assert "platform.ops" not in body["capabilities"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_control_plane_me_endpoint_invalid_bearer_401(tmp_path: Path) -> None:
    api = _build_api(tmp_path)
    identity_provider = StaticTokenIdentityProvider({})
    server = build_http_server(
        api=api, host="127.0.0.1", port=0, identity_provider=identity_provider
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    try:
        status, body = _http_json(
            method="GET",
            url=base + "/v1/me",
            headers={"Authorization": "Bearer bad"},
        )
        assert status == 401
        assert body["error"] == "invalid_bearer_token"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_get_v1_version_unauthenticated(tmp_path: Path) -> None:
    api = _build_api(tmp_path)
    server = build_http_server(api=api, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    try:
        status, body = _http_json(method="GET", url=base + "/v1/version")
        assert status == 200
        assert body.get("service") == "agentium"
        assert "git_sha" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_get_v1_approvals_list(tmp_path: Path) -> None:
    api = _build_api(tmp_path)
    server = build_http_server(api=api, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    headers = {"X-Tenant-Id": "tenant-a", "X-User-Id": "user-1", "X-Role": "admin"}
    try:
        status, body = _http_json(
            method="GET", url=base + "/v1/approvals?status=pending&limit=10", headers=headers
        )
        assert status == 200
        assert body["count"] == 0
        assert body["approvals"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_get_v1_runs_recent_aggregates_audit(tmp_path: Path) -> None:
    api = _build_api(tmp_path)
    server = build_http_server(api=api, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    headers = {"X-Tenant-Id": "tenant-a", "X-User-Id": "user-1", "X-Role": "admin"}
    try:
        run_status, _ = _http_json(
            method="POST",
            url=base + "/v1/turn",
            headers=headers,
            payload={
                "tool_name": "db_export",
                "args": {"dataset": "daily"},
                "run_id": "run-recent-1",
                "request_id": "req-r1",
                "trace_id": "trace-r1",
            },
        )
        assert run_status == 202
        status, body = _http_json(
            method="GET",
            url=base + "/v1/runs/recent?tenant_id=tenant-a&limit=20",
            headers=headers,
        )
        assert status == 200
        assert body["count"] >= 1
        run_ids = {row["run_id"] for row in body["runs"]}
        assert "run-recent-1" in run_ids
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.paper
def test_paper_http_run_manifest_declared_tools_blocks_non_listed_tool(tmp_path: Path) -> None:
    """Ingress passes manifest_declared_tools; undeclared tool is blocked at Runtime (H1)."""

    api = _build_api_two_tools(tmp_path)
    server = build_http_server(api=api, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    identity_headers = {
        "X-Tenant-Id": "tenant-a",
        "X-User-Id": "user-1",
        "X-Role": "admin",
    }
    try:
        run_status, run_body = _http_json(
            method="POST",
            url=base + "/v1/turn",
            headers=identity_headers,
            payload={
                "tool_name": "echo_tool",
                "args": {"text": "hi"},
                "run_id": "run-manifest-1",
                "request_id": "req-m1",
                "trace_id": "trace-m1",
                "run_manifest": {
                    "profile": "dev",
                    "build_id": "paper-1",
                    "declared_tools": ["db_export"],
                },
            },
        )
        assert run_status == 200
        assert run_body["status"] == "blocked"
        assert run_body.get("error_code") == "policy_denied"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_turn_ingress_audit_records_disposition(tmp_path: Path) -> None:
    """POST /v1/turn writes turn_ingress with message_disposition and mcp tier."""

    api = _build_api_two_tools(tmp_path)
    server = build_http_server(api=api, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    headers = {
        "X-Tenant-Id": "tenant-a",
        "X-User-Id": "user-1",
        "X-Role": "admin",
    }
    run_id = "run-ingress-1"
    try:
        st, body = _http_json(
            method="POST",
            url=base + "/v1/turn",
            headers=headers,
            payload={
                "tool_name": "echo_tool",
                "args": {"text": "hi"},
                "run_id": run_id,
                "request_id": "req-ing-1",
                "trace_id": "trace-ing-1",
                "message_disposition": "steer",
                "mcp_execution_tier": "code-exec-mcp",
            },
        )
        assert st == 200
        assert body["status"] == "completed"
        assert body.get("references") == []
        events = api.get_audit_events(run_id=run_id, event_type="turn_ingress", limit=50)
        assert len(events) == 1
        payload = events[0].payload
        assert payload["message_disposition"] == "steer"
        assert payload["mcp_execution_tier"] == "code-exec-mcp"
        assert payload["request_id"] == "req-ing-1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_get_tools_catalog_ok(tmp_path: Path) -> None:
    api = _build_api_two_tools(tmp_path)
    server = build_http_server(api=api, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    headers = {
        "X-Tenant-Id": "tenant-a",
        "X-User-Id": "user-1",
        "X-Role": "admin",
    }
    try:
        st, body = _http_json(method="GET", url=base + "/v1/tools", headers=headers)
        assert st == 200
        assert body["count"] >= 2
        names = {t["name"] for t in body["tools"]}
        assert "echo_tool" in names
        assert "db_export" in names
        echo = next(t for t in body["tools"] if t["name"] == "echo_tool")
        assert echo["risk_level"] == "low"
        assert isinstance(echo["capabilities"], list)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_get_tools_catalog_forbidden_guest_role(tmp_path: Path) -> None:
    api = _build_api_two_tools(tmp_path)
    server = build_http_server(api=api, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = "http://{host}:{port}".format(host=host, port=port)
    headers = {
        "X-Tenant-Id": "tenant-a",
        "X-User-Id": "user-1",
        "X-Role": "guest",
    }
    try:
        st, body = _http_json(method="GET", url=base + "/v1/tools", headers=headers)
        assert st == 403
        assert body["error"] == "forbidden"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
