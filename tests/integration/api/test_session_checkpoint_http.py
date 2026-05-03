"""Session checkpoints: create, list, restore injects timeline message."""

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
from agentium.governance.policy_engine import PolicyEngine
from agentium.infra.db.sqlite_store import (
    SqliteApprovalGate,
    SqliteAuditSink,
    SqliteRunMessageStore,
    SqliteSessionCheckpointStore,
)
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


def _write_allow_policy(tmp_path: Path) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(
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
        with request.urlopen(req, timeout=30) as resp:
            status = resp.getcode()
            body = json.loads(resp.read().decode("utf-8"))
            return status, body
    except error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        return exc.code, body


def _build_stack(tmp_path: Path) -> Tuple[
    SqliteAuditSink,
    SqliteApprovalGate,
    SqliteRunMessageStore,
    SqliteSessionCheckpointStore,
    threading.Thread,
    Any,
]:
    db_path = tmp_path / "cp.db"
    audit = SqliteAuditSink(db_path)
    gate = SqliteApprovalGate(db_path)
    msg_store = SqliteRunMessageStore(db_path)
    cp_store = SqliteSessionCheckpointStore(db_path)
    policy_engine = PolicyEngine.load(_write_allow_policy(tmp_path))
    ledger = BudgetLedger(
        {
            "t1": TenantBudget(token_limit=100_000, cost_limit=100.0, max_concurrency=8),
        }
    )
    registry = ToolRegistry(
        policy_engine=policy_engine,
        budget_ledger=ledger,
        audit_sink=audit,
        approval_gate=gate,
    )
    registry.register(
        ToolSpec(
            name="noop",
            capabilities=["utility"],
            risk_level="low",
            handler=lambda args: {"ok": True},
        )
    )
    runtime = AgentRuntime(tool_registry=registry)
    api = ControlPlaneAPI(runtime=runtime, approval_service=gate, audit_sink=audit)
    resources = HTTPControlPlaneResources(
        run_message_store=msg_store,
        session_checkpoint_store=cp_store,
    )
    server = build_http_server(
        api=api,
        host="127.0.0.1",
        port=0,
        audit_sink=audit,
        resources=resources,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return audit, gate, msg_store, cp_store, thread, server


@pytest.mark.integration
def test_session_checkpoint_create_list_restore_message(tmp_path: Path) -> None:
    audit, gate, msg_store, cp_store, thread, server = _build_stack(tmp_path)
    host, port = server.server_address
    base = f"http://{host}:{port}"
    headers = {
        "X-Tenant-Id": "t1",
        "X-User-Id": "u1",
        "X-Role": "admin",
    }
    session_id = "run-cp-1"

    try:
        st_create, body_create = _http_json(
            "POST",
            base + f"/v1/sessions/{session_id}/checkpoints",
            headers=headers,
            payload={"label": "snap-a", "payload": {"state": "step-2"}},
        )
        assert st_create == 201
        seq = int(body_create["seq"])
        assert seq == 1

        st_list, body_list = _http_json(
            "GET",
            base + f"/v1/sessions/{session_id}/checkpoints",
            headers=headers,
        )
        assert st_list == 200
        assert body_list["count"] == 1
        assert body_list["checkpoints"][0]["seq"] == 1
        assert body_list["checkpoints"][0]["label"] == "snap-a"

        st_restore, body_restore = _http_json(
            "POST",
            base + f"/v1/sessions/{session_id}/checkpoints/{seq}/restore",
            headers=headers,
        )
        assert st_restore == 200
        assert body_restore["restored_seq"] == seq

        st_msg, body_msg = _http_json(
            "GET",
            base + f"/v1/sessions/{session_id}/messages?limit=20",
            headers=headers,
        )
        assert st_msg == 200
        kinds = {m["kind"] for m in body_msg["messages"]}
        assert "checkpoint_restore" in kinds
        restored = next(m for m in body_msg["messages"] if m["kind"] == "checkpoint_restore")
        assert restored["body"]["checkpoint_seq"] == seq
        assert restored["body"]["snapshot"]["state"] == "step-2"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        audit.close()
        gate.close()
        msg_store.close()
        cp_store.close()
