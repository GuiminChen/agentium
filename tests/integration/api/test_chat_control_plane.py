"""Integration tests for TradeAgent-aligned ``/v1/chat/*`` HTTP endpoints."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib import error, request

import pytest

from agentium.ai_gateway.deepseek_chat import DeepSeekCompletionResult
from agentium.api.control_plane import ControlPlaneAPI
from agentium.api.http.resources import HTTPControlPlaneResources
from agentium.api.http_control_plane import build_http_server
from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.coordination.chat_turn_service import ChatTurnService
from agentium.core.agent_runtime import AgentRuntime
from agentium.core.run_cancellation import RunCancelRegistry
from agentium.governance.policy_engine import PolicyEngine
from agentium.infra.db.sqlite_chat_session_store import SqliteChatSessionStore
from agentium.infra.db.sqlite_store import SqliteApprovalGate, SqliteAuditSink, SqliteRunMessageStore
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


def _allow_policy(tmp_path: Path) -> Path:
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


class _FakeDeepSeek:
    """Returns deterministic completions without network."""

    def complete_chat(
        self,
        messages: Any,
        *,
        trace_id: str,
        request_id: str,
    ) -> DeepSeekCompletionResult:
        del messages, trace_id, request_id
        return DeepSeekCompletionResult(text="synthetic_reply", raw_finish_reason="stop")


def _http_json(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Dict[str, Any]]:
    data = b""
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data if payload is not None else None, method=method)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with request.urlopen(req, timeout=30) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _headers() -> Dict[str, str]:
    return {"X-Tenant-Id": "t-chat", "X-User-Id": "u1", "X-Role": "user"}


@pytest.mark.integration
def test_chat_sessions_crud_and_message_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "chat.db"
    audit = SqliteAuditSink(db_path)
    gate = SqliteApprovalGate(db_path)
    msg_store = SqliteRunMessageStore(db_path)
    chat_sess = SqliteChatSessionStore(db_path)
    cancel_reg = RunCancelRegistry()
    policy_engine = PolicyEngine.load(_allow_policy(tmp_path))
    ledger = BudgetLedger({"t-chat": TenantBudget(token_limit=10_000, cost_limit=10.0, max_concurrency=2)})
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
    runtime = AgentRuntime(tool_registry=registry, run_cancel_registry=cancel_reg)
    api = ControlPlaneAPI(runtime=runtime, approval_service=gate, audit_sink=audit)
    svc = ChatTurnService(
        run_message_store=msg_store,
        chat_session_store=chat_sess,
        deepseek_client=_FakeDeepSeek(),
        audit_sink=audit.append,
    )
    resources = HTTPControlPlaneResources(
        run_message_store=msg_store,
        chat_session_store=chat_sess,
        chat_turn_service=svc,
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
    try:
        host, port = server.server_address[:2]
        base = f"http://127.0.0.1:{int(port)}"
        hdrs = _headers()

        st, dup = _http_json("POST", f"{base}/v1/chat/sessions", {"session_id": "s1"}, hdrs)
        assert st == 201
        assert dup["session"]["session_id"] == "s1"

        st_conflict, body_conflict = _http_json(
            "POST", f"{base}/v1/chat/sessions", {"session_id": "s1"}, hdrs
        )
        assert st_conflict == 409
        assert body_conflict.get("error") == "SESSION_ID_EXISTS"

        st, lst = _http_json("GET", f"{base}/v1/chat/sessions?page=1&page_size=10", headers=hdrs)
        assert st == 200
        assert len(lst["items"]) == 1

        st, _ = _http_json("PUT", f"{base}/v1/chat/sessions/s1", {"title": "Hello"}, hdrs)
        assert st == 200

        st_empty, msgs0 = _http_json("GET", f"{base}/v1/chat/sessions/s1/messages", headers=hdrs)
        assert st_empty == 200
        assert msgs0["items"] == []

        st_send, ans = _http_json(
            "POST",
            f"{base}/v1/chat/messages",
            {"session_id": "s1", "content": "ping", "stream": False},
            hdrs,
        )
        assert st_send == 200
        assert ans.get("type") == "Answer"
        assert ans["content_blocks"][0]["text"] == "synthetic_reply"

        st_msgs, msgs1 = _http_json("GET", f"{base}/v1/chat/sessions/s1/messages", headers=hdrs)
        assert st_msgs == 200
        assert len(msgs1["items"]) == 1
        assert msgs1["items"][0]["query"] == "ping"

        st_del, deleted = _http_json("DELETE", f"{base}/v1/chat/sessions/s1", headers=hdrs)
        assert st_del == 200
        assert deleted["deleted"] is True

        st_gone, _ = _http_json("GET", f"{base}/v1/chat/sessions/s1", headers=hdrs)
        assert st_gone == 404
    finally:
        server.shutdown()
        server.server_close()
        for fn in (msg_store.close, chat_sess.close, gate.close, audit.close):
            try:
                fn()
            except Exception:
                pass
