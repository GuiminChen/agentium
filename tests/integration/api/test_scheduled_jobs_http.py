"""Integration tests for ``/v1/jobs`` scheduled job HTTP API."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib import error, request

import pytest

from tests.integration.api.test_chat_control_plane import (
    _FakeDeepSeek,
    _allow_policy,
    _integration_chat_settings,
)

from agentium.ai_gateway.deepseek_chat import DeepSeekCompletionResult, LlmUsageSnapshot
from agentium.api.control_plane import ControlPlaneAPI
from agentium.api.http.resources import HTTPControlPlaneResources
from agentium.api.http_control_plane import build_http_server
from agentium.app.settings import AppSettings
from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.coordination.chat_skill_prompt import build_skill_addon_text
from agentium.coordination.chat_turn_service import ChatTurnService
from agentium.coordination.scheduled_job_runner import ScheduledJobRunner
from agentium.core.agent_runtime import AgentRuntime
from agentium.core.run_cancellation import RunCancelRegistry
from agentium.governance.policy_engine import PolicyEngine
from agentium.infra.db.sqlite_chat_session_store import SqliteChatSessionStore
from agentium.infra.db.sqlite_scheduled_job_store import SqliteScheduledJobStore
from agentium.infra.db.sqlite_store import SqliteApprovalGate, SqliteAuditSink, SqliteRunMessageStore
from agentium.memory.backends.inmemory_backend import InMemoryBackend
from agentium.memory.chat_memory_lane_router import ChatMemoryLaneRouter
from agentium.memory.memory_service import MemoryService
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


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


def _sched_job_settings(tmp_path: Path) -> AppSettings:
    return _integration_chat_settings(tmp_path)


@pytest.mark.integration
def test_jobs_http_crud_trigger_and_guest_forbidden(tmp_path: Path) -> None:
    db_path = tmp_path / "agentium.db"
    audit = SqliteAuditSink(db_path)
    gate = SqliteApprovalGate(db_path)
    msg_store = SqliteRunMessageStore(db_path)
    chat_sess = SqliteChatSessionStore(db_path)
    sched_store = SqliteScheduledJobStore(db_path)
    cancel_reg = RunCancelRegistry()
    policy_engine = PolicyEngine.load(_allow_policy(tmp_path))
    ledger = BudgetLedger({"t-jobs": TenantBudget(token_limit=10_000, cost_limit=10.0, max_concurrency=2)})
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
    int_settings = _sched_job_settings(tmp_path)

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
    runner = ScheduledJobRunner(
        store=sched_store,
        chat_turn_service=svc,
        chat_session_store=chat_sess,
        settings=int_settings,
        audit_sink=audit.append,
        budget_service=ledger,
        notify_bridge=None,
    )
    resources = HTTPControlPlaneResources(
        run_message_store=msg_store,
        chat_session_store=chat_sess,
        chat_turn_service=svc,
        chat_memory_lane_router=lane_router,
        memory_service=memory_svc,
        tool_registry=registry,
        settings=int_settings,
        scheduled_job_store=sched_store,
        scheduled_job_runner=runner,
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
        _host, port = server.server_address[:2]
        base = f"http://127.0.0.1:{int(port)}"
        admin_h = {"X-Tenant-Id": "t-jobs", "X-User-Id": "u-admin", "X-Role": "admin"}
        guest_h = {"X-Tenant-Id": "t-jobs", "X-User-Id": "u-guest", "X-Role": "guest"}

        st403, _b403 = _http_json("GET", f"{base}/v1/jobs?page=1&page_size=10", headers=guest_h)
        assert st403 == 403

        job_body = {
            "name": "integration-job",
            "enabled": True,
            "task_kind": "chat_turn",
            "trigger": {"kind": "interval", "interval_seconds": 120},
            "session_binding": "named_persistent",
            "payload": {"message_content": "ping from job"},
        }
        st400, _ = _http_json(
            "POST",
            f"{base}/v1/jobs",
            {
                "name": "bad",
                "enabled": True,
                "task_kind": "chat_turn",
                "trigger": {"kind": "bogus"},
                "session_binding": "named_persistent",
                "payload": {"message_content": "x"},
            },
            headers=admin_h,
        )
        assert st400 == 400

        st201, created = _http_json("POST", f"{base}/v1/jobs", job_body, headers=admin_h)
        assert st201 == 201
        job_id = created["job_id"]
        assert created["budget_estimate_tokens"] is None

        st200l, lst = _http_json("GET", f"{base}/v1/jobs?page=1&page_size=10", headers=admin_h)
        assert st200l == 200
        assert any(item["job_id"] == job_id for item in lst["items"])

        st200g, one = _http_json("GET", f"{base}/v1/jobs/{job_id}", headers=admin_h)
        assert st200g == 200
        assert one["name"] == "integration-job"

        st202, _acc = _http_json("POST", f"{base}/v1/jobs/{job_id}/trigger", {}, headers=admin_h)
        assert st202 == 202

        st200r, runs = _http_json(
            "GET",
            f"{base}/v1/jobs/{job_id}/runs?page=1&page_size=10",
            headers=admin_h,
        )
        assert st200r == 200
        assert runs["pagination"]["total"] >= 1
    finally:
        server.shutdown()
        sched_store.close()


@pytest.mark.integration
def test_jobs_trigger_budget_skip_when_over_limit(tmp_path: Path) -> None:
    """Ledger token_limit below reserve estimate yields skipped run."""

    db_path = tmp_path / "agentium-budget.db"
    audit = SqliteAuditSink(db_path)
    gate = SqliteApprovalGate(db_path)
    msg_store = SqliteRunMessageStore(db_path)
    chat_sess = SqliteChatSessionStore(db_path)
    sched_store = SqliteScheduledJobStore(db_path)
    cancel_reg = RunCancelRegistry()
    policy_engine = PolicyEngine.load(_allow_policy(tmp_path))
    ledger = BudgetLedger({"t-b": TenantBudget(token_limit=50, cost_limit=10.0, max_concurrency=4)})
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

    class _CountDeepSeek(_FakeDeepSeek):
        def complete_chat(self, *args: Any, **kwargs: Any) -> DeepSeekCompletionResult:
            raise AssertionError("LLM should not run when budget denies")

    int_settings = replace(_sched_job_settings(tmp_path), scheduled_job_default_budget_estimate_tokens=100)

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
        deepseek_client=_CountDeepSeek(),
        audit_sink=audit.append,
        skill_addon=_addon,
        settings=int_settings,
        control_plane_api=api,
        tool_registry=registry,
        memory_lane_router=lane_router,
    )
    runner = ScheduledJobRunner(
        store=sched_store,
        chat_turn_service=svc,
        chat_session_store=chat_sess,
        settings=int_settings,
        audit_sink=audit.append,
        budget_service=ledger,
        notify_bridge=None,
    )
    resources = HTTPControlPlaneResources(
        run_message_store=msg_store,
        chat_session_store=chat_sess,
        chat_turn_service=svc,
        chat_memory_lane_router=lane_router,
        memory_service=memory_svc,
        tool_registry=registry,
        settings=int_settings,
        scheduled_job_store=sched_store,
        scheduled_job_runner=runner,
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
        _host, port = server.server_address[:2]
        base = f"http://127.0.0.1:{int(port)}"
        admin_h = {"X-Tenant-Id": "t-b", "X-User-Id": "u1", "X-Role": "admin"}
        job_body = {
            "name": "budget-job",
            "enabled": True,
            "task_kind": "chat_turn",
            "trigger": {"kind": "interval", "interval_seconds": 120},
            "session_binding": "named_persistent",
            "payload": {"message_content": "x"},
            "budget_estimate_tokens": 100,
        }
        st201, created = _http_json("POST", f"{base}/v1/jobs", job_body, headers=admin_h)
        assert st201 == 201
        job_id = created["job_id"]

        st202, _ = _http_json("POST", f"{base}/v1/jobs/{job_id}/trigger", {}, headers=admin_h)
        assert st202 == 202

        _st, runs = _http_json(
            "GET",
            f"{base}/v1/jobs/{job_id}/runs?page=1&page_size=10",
            headers=admin_h,
        )
        assert runs["items"]
        assert runs["items"][0]["status"] == "skipped"
        assert "budget" in (runs["items"][0].get("error_detail") or "")
    finally:
        server.shutdown()
        sched_store.close()


def _policy_denies_sched_manage_for_admin(tmp_path: Path) -> Path:
    path = tmp_path / "policy-sched-gate.yaml"
    path.write_text(
        "\n".join(
            [
                "version: pgate",
                "default_decision: allow",
                "default_reason: ok",
                "rules:",
                "  - id: deny_sched_manage_admin",
                "    decision: deny",
                "    reason: integration_sched_gate",
                "    tools:",
                "      - scheduled_job.manage",
                "    roles:",
                "      - admin",
            ]
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.integration
def test_jobs_policy_gate_denies_manage_when_yaml_blocks_admin(tmp_path: Path) -> None:
    db_path = tmp_path / "agentium-gate.db"
    audit = SqliteAuditSink(db_path)
    gate = SqliteApprovalGate(db_path)
    msg_store = SqliteRunMessageStore(db_path)
    chat_sess = SqliteChatSessionStore(db_path)
    sched_store = SqliteScheduledJobStore(db_path)
    cancel_reg = RunCancelRegistry()
    policy_engine = PolicyEngine.load(_policy_denies_sched_manage_for_admin(tmp_path))
    ledger = BudgetLedger({"t-gate": TenantBudget(token_limit=50_000, cost_limit=50.0, max_concurrency=4)})
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
    int_settings = replace(_sched_job_settings(tmp_path), scheduled_jobs_policy_gate_enabled=True)

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
    runner = ScheduledJobRunner(
        store=sched_store,
        chat_turn_service=svc,
        chat_session_store=chat_sess,
        settings=int_settings,
        audit_sink=audit.append,
        budget_service=ledger,
        notify_bridge=None,
    )
    resources = HTTPControlPlaneResources(
        run_message_store=msg_store,
        chat_session_store=chat_sess,
        chat_turn_service=svc,
        chat_memory_lane_router=lane_router,
        memory_service=memory_svc,
        tool_registry=registry,
        settings=int_settings,
        scheduled_job_store=sched_store,
        scheduled_job_runner=runner,
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
        _host, port = server.server_address[:2]
        base = f"http://127.0.0.1:{int(port)}"
        admin_h = {"X-Tenant-Id": "t-gate", "X-User-Id": "u-admin", "X-Role": "admin"}
        job_body = {
            "name": "gated",
            "enabled": True,
            "task_kind": "chat_turn",
            "trigger": {"kind": "interval", "interval_seconds": 120},
            "session_binding": "named_persistent",
            "payload": {"message_content": "x"},
        }
        st403, body = _http_json("POST", f"{base}/v1/jobs", job_body, headers=admin_h)
        assert st403 == 403
        assert body.get("error") == "policy_denied"
    finally:
        server.shutdown()
        sched_store.close()


@pytest.mark.integration
def test_jobs_list_runs_started_after_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "agentium-runs-filter.db"
    audit = SqliteAuditSink(db_path)
    gate = SqliteApprovalGate(db_path)
    msg_store = SqliteRunMessageStore(db_path)
    chat_sess = SqliteChatSessionStore(db_path)
    sched_store = SqliteScheduledJobStore(db_path)
    cancel_reg = RunCancelRegistry()
    policy_engine = PolicyEngine.load(_allow_policy(tmp_path))
    ledger = BudgetLedger({"t-rf": TenantBudget(token_limit=50_000, cost_limit=50.0, max_concurrency=4)})
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
    int_settings = _sched_job_settings(tmp_path)

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
    runner = ScheduledJobRunner(
        store=sched_store,
        chat_turn_service=svc,
        chat_session_store=chat_sess,
        settings=int_settings,
        audit_sink=audit.append,
        budget_service=ledger,
        notify_bridge=None,
    )
    resources = HTTPControlPlaneResources(
        run_message_store=msg_store,
        chat_session_store=chat_sess,
        chat_turn_service=svc,
        chat_memory_lane_router=lane_router,
        memory_service=memory_svc,
        tool_registry=registry,
        settings=int_settings,
        scheduled_job_store=sched_store,
        scheduled_job_runner=runner,
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
        _host, port = server.server_address[:2]
        base = f"http://127.0.0.1:{int(port)}"
        admin_h = {"X-Tenant-Id": "t-rf", "X-User-Id": "u1", "X-Role": "admin"}
        job_body = {
            "name": "runs-filter",
            "enabled": True,
            "task_kind": "chat_turn",
            "trigger": {"kind": "interval", "interval_seconds": 120},
            "session_binding": "named_persistent",
            "payload": {"message_content": "ping"},
        }
        _st201, created = _http_json("POST", f"{base}/v1/jobs", job_body, headers=admin_h)
        job_id = created["job_id"]
        _st202, _ = _http_json("POST", f"{base}/v1/jobs/{job_id}/trigger", {}, headers=admin_h)
        _st_runs, runs1 = _http_json(
            "GET",
            f"{base}/v1/jobs/{job_id}/runs?page=1&page_size=10",
            headers=admin_h,
        )
        rid_old = runs1["items"][0]["run_id"]
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE scheduled_job_runs SET started_at = ? WHERE run_id = ?",
            ("2020-01-01T00:00:00+00:00", rid_old),
        )
        conn.commit()
        conn.close()
        _st202b, _ = _http_json("POST", f"{base}/v1/jobs/{job_id}/trigger", {}, headers=admin_h)
        assert _st202b == 202
        split = "2025-06-01T00:00:00"
        _st_f, filtered = _http_json(
            "GET",
            f"{base}/v1/jobs/{job_id}/runs?page=1&page_size=10&started_after={split}",
            headers=admin_h,
        )
        assert _st_f == 200
        assert filtered["pagination"]["total"] == 1
    finally:
        server.shutdown()
        sched_store.close()


@pytest.mark.integration
def test_jobs_webhook_hmac_and_idempotency(tmp_path: Path) -> None:
    db_path = tmp_path / "agentium-webhook.db"
    audit = SqliteAuditSink(db_path)
    gate = SqliteApprovalGate(db_path)
    msg_store = SqliteRunMessageStore(db_path)
    chat_sess = SqliteChatSessionStore(db_path)
    sched_store = SqliteScheduledJobStore(db_path)
    cancel_reg = RunCancelRegistry()
    policy_engine = PolicyEngine.load(_allow_policy(tmp_path))
    ledger = BudgetLedger({"t-wh": TenantBudget(token_limit=50_000, cost_limit=50.0, max_concurrency=4)})
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
    secret = "integration-wh-secret"
    int_settings = replace(_sched_job_settings(tmp_path), scheduled_jobs_webhook_secret=secret)

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
    runner = ScheduledJobRunner(
        store=sched_store,
        chat_turn_service=svc,
        chat_session_store=chat_sess,
        settings=int_settings,
        audit_sink=audit.append,
        budget_service=ledger,
        notify_bridge=None,
    )
    resources = HTTPControlPlaneResources(
        run_message_store=msg_store,
        chat_session_store=chat_sess,
        chat_turn_service=svc,
        chat_memory_lane_router=lane_router,
        memory_service=memory_svc,
        tool_registry=registry,
        settings=int_settings,
        scheduled_job_store=sched_store,
        scheduled_job_runner=runner,
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
        _host, port = server.server_address[:2]
        base = f"http://127.0.0.1:{int(port)}"
        admin_h = {"X-Tenant-Id": "t-wh", "X-User-Id": "u1", "X-Role": "admin"}
        job_body = {
            "name": "wh-job",
            "enabled": True,
            "task_kind": "chat_turn",
            "trigger": {"kind": "interval", "interval_seconds": 120},
            "session_binding": "named_persistent",
            "payload": {"message_content": "hook"},
        }
        _st201, created = _http_json("POST", f"{base}/v1/jobs", job_body, headers=admin_h)
        job_id = created["job_id"]
        payload_dict = {"job_id": job_id, "tenant_id": "t-wh"}
        raw = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")
        bad_hdr = {"Content-Type": "application/json", "X-Agentium-Job-Signature": "deadbeef"}
        st401, _ = _http_json("POST", f"{base}/v1/jobs/webhook-trigger", payload_dict, headers=bad_hdr)
        assert st401 == 401
        mac = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        ok_hdr = {
            "Content-Type": "application/json",
            "X-Agentium-Job-Signature": mac,
            "Idempotency-Key": "k-integration-1",
        }
        st202a, body_a = _http_json("POST", f"{base}/v1/jobs/webhook-trigger", payload_dict, headers=ok_hdr)
        assert st202a == 202
        assert body_a.get("deduplicated") is not True
        _st_runs, runs_mid = _http_json(
            "GET",
            f"{base}/v1/jobs/{job_id}/runs?page=1&page_size=10",
            headers=admin_h,
        )
        n_mid = runs_mid["pagination"]["total"]
        st202b, body_b = _http_json("POST", f"{base}/v1/jobs/webhook-trigger", payload_dict, headers=ok_hdr)
        assert st202b == 202
        assert body_b.get("deduplicated") is True
        _st_runs2, runs_after = _http_json(
            "GET",
            f"{base}/v1/jobs/{job_id}/runs?page=1&page_size=10",
            headers=admin_h,
        )
        assert runs_after["pagination"]["total"] == n_mid
    finally:
        server.shutdown()
        sched_store.close()


@pytest.mark.integration
def test_jobs_create_cron_persisted_next_run(tmp_path: Path) -> None:
    pytest.importorskip("croniter")
    db_path = tmp_path / "agentium-cron.db"
    audit = SqliteAuditSink(db_path)
    gate = SqliteApprovalGate(db_path)
    msg_store = SqliteRunMessageStore(db_path)
    chat_sess = SqliteChatSessionStore(db_path)
    sched_store = SqliteScheduledJobStore(db_path)
    cancel_reg = RunCancelRegistry()
    policy_engine = PolicyEngine.load(_allow_policy(tmp_path))
    ledger = BudgetLedger({"t-cr": TenantBudget(token_limit=50_000, cost_limit=50.0, max_concurrency=4)})
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
    int_settings = _sched_job_settings(tmp_path)

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
    runner = ScheduledJobRunner(
        store=sched_store,
        chat_turn_service=svc,
        chat_session_store=chat_sess,
        settings=int_settings,
        audit_sink=audit.append,
        budget_service=ledger,
        notify_bridge=None,
    )
    resources = HTTPControlPlaneResources(
        run_message_store=msg_store,
        chat_session_store=chat_sess,
        chat_turn_service=svc,
        chat_memory_lane_router=lane_router,
        memory_service=memory_svc,
        tool_registry=registry,
        settings=int_settings,
        scheduled_job_store=sched_store,
        scheduled_job_runner=runner,
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
        _host, port = server.server_address[:2]
        base = f"http://127.0.0.1:{int(port)}"
        admin_h = {"X-Tenant-Id": "t-cr", "X-User-Id": "u1", "X-Role": "admin"}
        job_body = {
            "name": "cron-job",
            "enabled": True,
            "task_kind": "chat_turn",
            "trigger": {"kind": "cron", "cron_expression": "0 * * * *"},
            "session_binding": "named_persistent",
            "payload": {"message_content": "cron ping"},
        }
        st201, created = _http_json("POST", f"{base}/v1/jobs", job_body, headers=admin_h)
        assert st201 == 201
        assert created["trigger"]["kind"] == "cron"
        assert created.get("next_run_at_unix_ms") is not None
    finally:
        server.shutdown()
        sched_store.close()


class _UsageReportingDeepSeek(_FakeDeepSeek):
    """Reports cumulative tokens similar to OpenAI-compatible providers."""

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
        return DeepSeekCompletionResult(
            text="usage_reply",
            raw_finish_reason="stop",
            usage=LlmUsageSnapshot(prompt_tokens=10, completion_tokens=740, total_tokens=750),
        )


@pytest.mark.integration
def test_jobs_budget_commit_uses_reported_llm_usage(tmp_path: Path) -> None:
    db_path = tmp_path / "agentium-usage.db"
    audit = SqliteAuditSink(db_path)
    gate = SqliteApprovalGate(db_path)
    msg_store = SqliteRunMessageStore(db_path)
    chat_sess = SqliteChatSessionStore(db_path)
    sched_store = SqliteScheduledJobStore(db_path)
    cancel_reg = RunCancelRegistry()
    policy_engine = PolicyEngine.load(_allow_policy(tmp_path))
    ledger = BudgetLedger({"t-use": TenantBudget(token_limit=500_000, cost_limit=500.0, max_concurrency=4)})
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
    int_settings = replace(_sched_job_settings(tmp_path), scheduled_job_default_budget_estimate_tokens=500)

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
        deepseek_client=_UsageReportingDeepSeek(),
        audit_sink=audit.append,
        skill_addon=_addon,
        settings=int_settings,
        control_plane_api=api,
        tool_registry=registry,
        memory_lane_router=lane_router,
    )
    runner = ScheduledJobRunner(
        store=sched_store,
        chat_turn_service=svc,
        chat_session_store=chat_sess,
        settings=int_settings,
        audit_sink=audit.append,
        budget_service=ledger,
        notify_bridge=None,
    )
    resources = HTTPControlPlaneResources(
        run_message_store=msg_store,
        chat_session_store=chat_sess,
        chat_turn_service=svc,
        chat_memory_lane_router=lane_router,
        memory_service=memory_svc,
        tool_registry=registry,
        settings=int_settings,
        scheduled_job_store=sched_store,
        scheduled_job_runner=runner,
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
        _host, port = server.server_address[:2]
        base = f"http://127.0.0.1:{int(port)}"
        admin_h = {"X-Tenant-Id": "t-use", "X-User-Id": "u1", "X-Role": "admin"}
        job_body = {
            "name": "usage-job",
            "enabled": True,
            "task_kind": "chat_turn",
            "trigger": {"kind": "interval", "interval_seconds": 120},
            "session_binding": "named_persistent",
            "payload": {"message_content": "x"},
            "budget_estimate_tokens": 500,
        }
        _st201, created = _http_json("POST", f"{base}/v1/jobs", job_body, headers=admin_h)
        job_id = created["job_id"]
        _st202, _ = _http_json("POST", f"{base}/v1/jobs/{job_id}/trigger", {}, headers=admin_h)
        snap = ledger.usage_for_tenant("t-use")
        assert snap is not None
        assert snap.tokens_used == 750
    finally:
        server.shutdown()
        sched_store.close()
