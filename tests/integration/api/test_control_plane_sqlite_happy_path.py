"""SQLite-backed HITL + runs aggregate + audit export + tenant isolation via HTTP."""

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
from agentium.governance.policy_engine import PolicyEngine
from agentium.infra.db.sqlite_store import SqliteApprovalGate, SqliteAuditSink
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


def _build_sqlite_api(tmp_path: Path, db_path: Path) -> tuple[ControlPlaneAPI, SqliteAuditSink, SqliteApprovalGate]:
    policy_engine = PolicyEngine.load(_write_policy(tmp_path))
    ledger = BudgetLedger(
        {
            "tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1),
            "tenant-b": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1),
        }
    )
    audit = SqliteAuditSink(db_path)
    gate = SqliteApprovalGate(db_path)
    registry = ToolRegistry(
        policy_engine=policy_engine,
        budget_ledger=ledger,
        audit_sink=audit,
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
    api = ControlPlaneAPI(runtime=runtime, approval_service=gate, audit_sink=audit)
    return api, audit, gate


@pytest.mark.integration
def test_sqlite_control_plane_hitl_runs_audit_and_tenant_isolation(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "agentium.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    api, audit, gate = _build_sqlite_api(tmp_path, db_path)
    server = build_http_server(api=api, host="127.0.0.1", port=0, audit_sink=audit)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    tenant_a_headers = {
        "X-Tenant-Id": "tenant-a",
        "X-User-Id": "user-a",
        "X-Role": "admin",
    }
    tenant_b_headers = {
        "X-Tenant-Id": "tenant-b",
        "X-User-Id": "user-b",
        "X-Role": "user",
    }
    run_id = "sqlite-hitl-run-1"
    try:
        st_turn, body_turn = _http_json(
            method="POST",
            url=base + "/v1/turn",
            headers=tenant_a_headers,
            payload={
                "tool_name": "db_export",
                "args": {"dataset": "daily"},
                "run_id": run_id,
                "request_id": "req-turn-1",
                "trace_id": "trace-turn-1",
                "message_disposition": "collect",
                "mcp_execution_tier": "direct-tool",
            },
        )
        assert st_turn == 202
        assert body_turn["status"] == "pending_approval"
        approval_id = body_turn["approval_id"]
        assert approval_id

        st_list, body_list = _http_json(
            method="GET",
            url=base + f"/v1/approvals?tenant_id=tenant-a&limit=50",
            headers=tenant_a_headers,
        )
        assert st_list == 200
        assert body_list["count"] >= 1
        assert any(a["approval_id"] == approval_id for a in body_list["approvals"])

        st_recent, body_recent = _http_json(
            method="GET",
            url=base + "/v1/runs/recent?tenant_id=tenant-a&limit=20",
            headers=tenant_a_headers,
        )
        assert st_recent == 200
        assert any(r["run_id"] == run_id for r in body_recent["runs"])

        st_dec, body_dec = _http_json(
            method="POST",
            url=base + f"/v1/approvals/{approval_id}/decision",
            headers=tenant_a_headers,
            payload={
                "decision": "approve",
                "approver_id": "reviewer-1",
                "comment": "ok",
            },
        )
        assert st_dec == 200
        assert body_dec["applied"] is True

        st_res, body_res = _http_json(
            method="POST",
            url=base + "/v1/turns/resume",
            headers=tenant_a_headers,
            payload={
                "tool_name": "db_export",
                "args": {"dataset": "daily"},
                "run_id": run_id,
                "request_id": "req-resume-1",
                "trace_id": "trace-resume-1",
                "approval_id": approval_id,
                "message_disposition": "followup",
                "mcp_execution_tier": "code-exec-mcp",
            },
        )
        assert st_res == 200
        assert body_res["status"] == "completed"

        ingress = api.get_audit_events(run_id=run_id, event_type="turn_ingress", limit=50)
        assert len(ingress) >= 2
        by_req = {ev.payload["request_id"]: ev.payload for ev in ingress}
        assert by_req["req-turn-1"]["message_disposition"] == "collect"
        assert by_req["req-turn-1"]["mcp_execution_tier"] == "direct-tool"
        assert by_req["req-resume-1"]["message_disposition"] == "followup"
        assert by_req["req-resume-1"]["mcp_execution_tier"] == "code-exec-mcp"

        st_tl, body_tl = _http_json(
            method="GET",
            url=base + f"/v1/runs/{run_id}/timeline?limit=100",
            headers=tenant_a_headers,
        )
        assert st_tl == 200
        assert body_tl["count"] >= 1

        st_ex, body_ex = _http_json(
            method="GET",
            url=base + f"/v1/audit/export?run_id={run_id}&redact=1",
            headers=tenant_a_headers,
        )
        assert st_ex == 200
        assert body_ex["run_id"] == run_id
        for ev in body_ex["events"]:
            p = ev.get("payload")
            if isinstance(p, dict) and p:
                assert all(v == "[REDACTED]" for v in p.values())

        st_x, body_x = _http_json(
            method="GET",
            url=base + f"/v1/runs/{run_id}/timeline?limit=50",
            headers=tenant_b_headers,
        )
        assert st_x == 403
        assert body_x["error"] == "tenant_mismatch"

        st_x2, body_x2 = _http_json(
            method="GET",
            url=base + f"/v1/audit/export?run_id={run_id}&redact=1",
            headers=tenant_b_headers,
        )
        assert st_x2 == 403
        assert body_x2["error"] == "tenant_mismatch"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        audit.close()
        gate.close()
