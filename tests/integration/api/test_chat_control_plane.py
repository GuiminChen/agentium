"""Integration tests for TradeAgent-aligned ``/v1/chat/*`` HTTP endpoints."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib import error, request

import pytest

from tests.helpers.app_settings_test_baseline import app_settings_extended_dict_for_data_dir
from tests.helpers.chat_ingress_test_defaults import chat_ingress_memory_fields, chat_ingress_off_fields

from agentium.ai_gateway.deepseek_chat import DeepSeekCompletionResult
from agentium.api.control_plane import ControlPlaneAPI
from agentium.api.http.resources import HTTPControlPlaneResources
from agentium.api.http_control_plane import build_http_server
from agentium.app.plugins_config import load_plugins_config
from agentium.app.settings import AppSettings
from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.coordination.chat_ingress.factory import build_chat_ingress_coordinator
from agentium.coordination.chat_skill_prompt import build_skill_addon_text
from agentium.coordination.chat_turn_service import ChatTurnService
from agentium.core.agent_runtime import AgentRuntime
from agentium.core.run_cancellation import RunCancelRegistry
from agentium.governance.policy_engine import PolicyEngine
from agentium.infra.db.sqlite_chat_session_store import SqliteChatSessionStore
from agentium.infra.db.sqlite_store import SqliteApprovalGate, SqliteAuditSink, SqliteRunMessageStore
from agentium.memory.backends.inmemory_backend import InMemoryBackend
from agentium.memory.chat_memory_lane_router import ChatMemoryLaneRouter
from agentium.memory.memory_service import MemoryService
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
        thinking: Any = None,
        model_override: Any = None,
    ) -> DeepSeekCompletionResult:
        del messages, trace_id, request_id, thinking, model_override
        return DeepSeekCompletionResult(text="synthetic_reply", raw_finish_reason="stop")

    def iter_complete_chat(
        self,
        messages: Any,
        *,
        trace_id: str,
        request_id: str,
        thinking: Any = None,
        model_override: Any = None,
    ) -> Any:
        del messages, trace_id, request_id, thinking, model_override
        from agentium.ai_gateway.deepseek_chat import DeepSeekStreamDelta

        yield DeepSeekStreamDelta(content="synthetic", reasoning="", finish_reason=None)
        yield DeepSeekStreamDelta(content="_reply", reasoning="", finish_reason="stop")

    def complete_chat_round(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise NotImplementedError("not used in this integration test")


class _HoldDeepSeek:
    """Blocks inside ``complete_chat`` until ``release`` is set (lease stays held)."""

    def __init__(self) -> None:
        self.llm_entered = threading.Event()
        self.llm_release = threading.Event()

    def complete_chat(
        self,
        messages: Any,
        *,
        trace_id: str,
        request_id: str,
        thinking: Any = None,
        model_override: Any = None,
    ) -> DeepSeekCompletionResult:
        del messages, trace_id, request_id, thinking, model_override
        self.llm_entered.set()
        if not self.llm_release.wait(timeout=120):
            raise RuntimeError("test deadlock: release not set")
        return DeepSeekCompletionResult(text="synthetic_reply", raw_finish_reason="stop")

    def iter_complete_chat(
        self,
        messages: Any,
        *,
        trace_id: str,
        request_id: str,
        thinking: Any = None,
        model_override: Any = None,
    ) -> Any:
        del messages, trace_id, request_id, thinking, model_override
        from agentium.ai_gateway.deepseek_chat import DeepSeekStreamDelta

        self.llm_entered.set()
        if not self.llm_release.wait(timeout=120):
            raise RuntimeError("test deadlock: release not set")
        yield DeepSeekStreamDelta(content="synthetic", reasoning="", finish_reason=None)
        yield DeepSeekStreamDelta(content="_reply", reasoning="", finish_reason="stop")

    def complete_chat_round(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise NotImplementedError("not used in this integration test")


def _parse_sse_events(raw_body: bytes) -> list[dict[str, Any]]:
    text = raw_body.decode("utf-8")
    out: list[dict[str, Any]] = []
    for block in text.split("\n\n"):
        blk = block.strip()
        if not blk:
            continue
        line = next((ln for ln in blk.split("\n") if ln.strip().startswith("data:")), "")
        payload = line.split(":", 1)[1].strip() if line else ""
        if not payload:
            continue
        out.append(json.loads(payload))
    return out


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


def _http_sse_post(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, list[dict[str, Any]]]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with request.urlopen(req, timeout=30) as resp:
        code = resp.getcode()
        raw = resp.read()
    return code, _parse_sse_events(raw)


def _headers() -> Dict[str, str]:
    return {"X-Tenant-Id": "t-chat", "X-User-Id": "u1", "X-Role": "user"}


def _integration_chat_settings(
    tmp_path: Path,
    *,
    memory_ingress: bool = False,
    ingress_debounce_ms: Optional[int] = None,
) -> AppSettings:
    plugins = tmp_path / "chat-int-plugins.yaml"
    plugins.write_text(
        "orchestration:\n  backend: native\nmemory:\n  backend: memory\nevolution:\n  plugin: native\n",
        encoding="utf-8",
    )
    repo = tmp_path / "chat-int-repo"
    repo.mkdir(parents=True, exist_ok=True)
    usr_skills = tmp_path / "chat-int-uskills"
    usr_skills.mkdir(parents=True, exist_ok=True)
    ig: Dict[str, Any]
    if memory_ingress:
        ig = dict(chat_ingress_memory_fields(tmp_path))
    else:
        ig = dict(chat_ingress_off_fields(tmp_path))
    if ingress_debounce_ms is not None:
        ig["chat_ingress_debounce_ms"] = max(0, int(ingress_debounce_ms))
    return AppSettings(
        profile="dev",  # type: ignore[arg-type]
        host="127.0.0.1",
        port=8765,
        policy_path=_allow_policy(tmp_path),
        data_dir=tmp_path,
        plugins_config_path=plugins,
        plugins=load_plugins_config(plugins),
        approval_backend="memory",
        audit_backend="memory",
        identity_mode="hybrid",
        require_run_manifest=False,
        expected_run_manifest_sha256=None,
        background_enabled=False,
        background_interval_seconds=30.0,
        background_noise_rps_pause=0.0,
        telemetry_mode="null",
        default_tenant_token_limit=10000,
        default_tenant_cost_limit=10.0,
        default_tenant_max_concurrency=2,
        sqlite_approval_ttl_seconds=None,
        emergence_node_warn=200,
        emergence_node_hard=500,
        emergence_outbound_warn=30,
        emergence_outbound_hard=60,
        outbound_rate_limit_per_minute=60,
        policy_release_hmac_secret=None,
        grafana_base_url=None,
        tempo_base_url=None,
        domain_packs_root=None,
        repo_root=repo,
        skills_project_root=None,
        skills_user_root=usr_skills,
        skills_config_root=None,
        oidc_issuer_configs=(),
        lsp_upstream_url=None,
        deepseek_api_key=None,
        deepseek_base_url="https://api.deepseek.com",
        chat_completion_model="deepseek-v4-flash",
        chat_completion_timeout_seconds=120.0,
        chat_skill_body_max_chars=8000,
        chat_agent_tools_enabled=False,
        chat_agent_max_tool_rounds=8,
        chat_mid_semantic_memory_enabled=False,
        chat_session_running_summary_enabled=False,
        workspace_agent_persona_max_chars=4096,
        workspace_agent_max_skill_tags=8,
        workspace_agent_max_tool_allowlist=24,
        deepseek_thinking_enabled=True,
        deepseek_reasoning_effort="high",
        deepseek_inject_think_max_instruction=True,
        deepseek_dsml_tool_prompt_enabled=True,
        persona_templates_extra_root=None,
        log_file_path=None,
        log_file_backup_count=14,
        log_to_console=True,
        chat_auto_session_title_enabled=False,
        deferred_tasks_enabled=False,
        deferred_thread_pool_size=4,
        deferred_task_backend="thread",
        redis_url=None,
        **app_settings_extended_dict_for_data_dir(tmp_path),
        **ig,
    )


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
    int_settings = _integration_chat_settings(tmp_path)

    def _addon(tag: str) -> str:
        return build_skill_addon_text(tag, int_settings)

    memory_svc = MemoryService(backend=InMemoryBackend(), audit_sink=audit.append)

    lane_router = ChatMemoryLaneRouter.single_backend(
        sessions=chat_sess,
        memory_service=memory_svc,
        yaml_primary_backend="memory",
    )

    svc = ChatTurnService(
        run_message_store=msg_store,
        chat_session_store=chat_sess,
        deepseek_client=_FakeDeepSeek(),
        audit_sink=audit.append,
        skill_addon=_addon,
        settings=int_settings,
        control_plane_api=api,
        tool_registry=registry,
        memory_lane_router=lane_router,
    )
    resources = HTTPControlPlaneResources(
        run_message_store=msg_store,
        chat_session_store=chat_sess,
        chat_turn_service=svc,
        chat_memory_lane_router=lane_router,
        memory_service=memory_svc,
        tool_registry=registry,
        settings=int_settings,
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

        st_mem_miss, body_mem_miss = _http_json(
            "GET", f"{base}/v1/chat/sessions/no-such-session-yet/memory", headers=hdrs
        )
        assert st_mem_miss == 200
        assert body_mem_miss.get("items") == []

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

        st_sk, sk_body = _http_json("GET", f"{base}/v1/chat/skill-options", headers=hdrs)
        assert st_sk == 200
        assert any(x.get("id") == "workspace_agent" for x in sk_body["items"])

        st_pt, pt_body = _http_json("GET", f"{base}/v1/chat/persona-templates", headers=hdrs)
        assert st_pt == 200
        pt_ids = {str(x.get("role_id") or "") for x in pt_body.get("items", [])}
        assert "default" in pt_ids and "coding_partner" in pt_ids

        st_wa, sess_wa = _http_json(
            "POST",
            f"{base}/v1/chat/sessions",
            {
                "session_id": "s-agent",
                "title": "agent cfg",
                "workspace_agent": {
                    "skill_tags": ["workspace_agent"],
                    "chat_tool_allowlist": ["noop"],
                    "persona_identity_md": "You are a test persona.",
                },
            },
            hdrs,
        )
        assert st_wa == 201
        meta_wa = sess_wa["session"]["metadata"]["workspace_agent"]
        assert meta_wa["chat_tool_allowlist"] == ["noop"]
        assert meta_wa["persona_identity_md"].startswith("You are a test persona.")

        st_put_skill, sess_skill = _http_json(
            "PUT",
            f"{base}/v1/chat/sessions/s1",
            {"skill": "workspace_agent"},
            hdrs,
        )
        assert st_put_skill == 200
        assert sess_skill["session"]["skill"] == "workspace_agent"

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

        st_stream, sse_events = _http_sse_post(
            f"{base}/v1/chat/messages",
            {
                "session_id": "s1",
                "content": "streaming turn",
                "auto_ingress": False,
                "stream": True,
                "enable_agent_tools": False,
            },
            hdrs,
        )
        assert st_stream == 200
        kinds = [row.get("event") for row in sse_events]
        assert kinds[0] == "start"
        assert "delta" in kinds
        assert kinds[-1] == "done"
        assert sse_events[-1].get("answer") == "synthetic_reply"

        st_msgs2, msgs2 = _http_json("GET", f"{base}/v1/chat/sessions/s1/messages", headers=hdrs)
        assert st_msgs2 == 200
        assert len(msgs2["items"]) == 2
        assert msgs2["items"][1]["query"] == "streaming turn"

        st_mem, mem_body = _http_json("GET", f"{base}/v1/chat/sessions/s1/memory", headers=hdrs)
        assert st_mem == 200
        mem_items = mem_body.get("items") or []
        assert any(str(row.get("payload", {}).get("run_id")) == "s1" for row in mem_items)
        roles = {str(row.get("payload", {}).get("role") or "") for row in mem_items}
        assert "user" in roles and "assistant" in roles

        pair_id = str(ans.get("message_id") or "")
        assert pair_id
        st_reg, ans_reg = _http_json(
            "POST",
            f"{base}/v1/chat/messages",
            {
                "session_id": "s1",
                "content": "",
                "stream": False,
                "regenerate_from_message_id": pair_id,
            },
            hdrs,
        )
        assert st_reg == 200
        assert ans_reg.get("type") == "Answer"

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


@pytest.mark.integration
def test_followup_returns_202_while_chat_turn_holds_lease(tmp_path: Path) -> None:
    """With memory ingress, a followup while LLM work is blocked should queue (HTTP 202)."""

    db_path = tmp_path / "chat_blocked.db"
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
    int_settings = _integration_chat_settings(
        tmp_path,
        memory_ingress=True,
        ingress_debounce_ms=0,
    )
    ingress = build_chat_ingress_coordinator(int_settings)
    assert ingress is not None
    hold = _HoldDeepSeek()

    def _addon(tag: str) -> str:
        return build_skill_addon_text(tag, int_settings)

    memory_svc = MemoryService(backend=InMemoryBackend(), audit_sink=audit.append)

    lane_router = ChatMemoryLaneRouter.single_backend(
        sessions=chat_sess,
        memory_service=memory_svc,
        yaml_primary_backend="memory",
    )

    svc = ChatTurnService(
        run_message_store=msg_store,
        chat_session_store=chat_sess,
        deepseek_client=hold,
        audit_sink=audit.append,
        skill_addon=_addon,
        settings=int_settings,
        control_plane_api=api,
        tool_registry=registry,
        memory_lane_router=lane_router,
        ingress_coordinator=ingress,
    )
    resources = HTTPControlPlaneResources(
        run_message_store=msg_store,
        chat_session_store=chat_sess,
        chat_turn_service=svc,
        chat_memory_lane_router=lane_router,
        memory_service=memory_svc,
        tool_registry=registry,
        settings=int_settings,
    )
    server = build_http_server(
        api=api,
        host="127.0.0.1",
        port=0,
        audit_sink=audit,
        resources=resources,
    )
    bg = threading.Thread(target=server.serve_forever, daemon=True)
    bg.start()
    try:
        _h, port = server.server_address[:2]
        base = f"http://127.0.0.1:{int(port)}"
        hdrs = _headers()

        st_sess, _ = _http_json("POST", f"{base}/v1/chat/sessions", {"session_id": "s_block"}, hdrs)
        assert st_sess == 201

        first_http: list[tuple[int, Dict[str, Any]]] = []

        def _first_turn() -> None:
            first_http.append(
                _http_json(
                    "POST",
                    f"{base}/v1/chat/messages",
                    {
                        "session_id": "s_block",
                        "content": "hold lease",
                        "stream": False,
                        "auto_ingress": False,
                        "message_disposition": "collect",
                    },
                    hdrs,
                )
            )

        t1 = threading.Thread(target=_first_turn, daemon=True)
        t1.start()
        assert hold.llm_entered.wait(timeout=10)

        st_202, body_202 = _http_json(
            "POST",
            f"{base}/v1/chat/messages",
            {
                "session_id": "s_block",
                "content": "queued followup",
                "stream": False,
                "auto_ingress": False,
                "message_disposition": "followup",
            },
            hdrs,
        )
        assert st_202 == 202
        assert body_202.get("ingress_kind") == "followup"
        assert body_202.get("queue_depth") == 1

        hold.llm_release.set()
        t1.join(timeout=30)
        assert len(first_http) == 1
        assert first_http[0][0] == 200
    finally:
        server.shutdown()
        server.server_close()
        for fn in (msg_store.close, chat_sess.close, gate.close, audit.close):
            try:
                fn()
            except Exception:
                pass
