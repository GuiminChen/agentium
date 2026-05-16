"""HTTP research jobs with harness task lock (P2 evidence)."""

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
from agentium.app.settings import load_settings
from agentium.coordination.research_job import ResearchJobService
from agentium.coordination.task_lock.sqlite_backend import SqliteTaskLockBackend
from agentium.core.agent_runtime import AgentRuntime
from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


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


@pytest.mark.integration
def test_research_jobs_post_task_lock_blocks_second_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.setenv("AGENTIUM_FEATURE_TASK_LOCK", "1")
    settings = load_settings()

    policy = tmp_path / "pol.yaml"
    policy.write_text(
        "version: t\ndefault_decision: allow\ndefault_reason: ok\nrules: []\n",
        encoding="utf-8",
    )
    pe = PolicyEngine.load(policy)
    registry = ToolRegistry(
        policy_engine=pe,
        budget_ledger=BudgetLedger(
            {"tenant-http-lock": TenantBudget(1000, 10.0, 4)}
        ),
        audit_sink=InMemoryAuditSink(),
        approval_gate=ApprovalGate(),
    )
    registry.register(
        ToolSpec(name="echo_tool", capabilities=["echo"], risk_level="low", handler=lambda a: a)
    )
    runtime = AgentRuntime(tool_registry=registry)
    api = ControlPlaneAPI(runtime=runtime, approval_service=ApprovalGate())
    lock_db = tmp_path / "tl_http.db"
    svc = ResearchJobService(settings=settings, task_lock_backend=SqliteTaskLockBackend(path=lock_db))
    resources = HTTPControlPlaneResources(research_job_service=svc, settings=settings)
    server = build_http_server(
        api=api, host="127.0.0.1", port=0, resources=resources, identity_mode="header"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    headers = {
        "X-Tenant-Id": "tenant-http-lock",
        "X-User-Id": "user-1",
        "X-Role": "user",
    }
    harness = {"lock_resource_keys": ["exclusive-resource"]}
    try:
        st1, body1 = _http_json(
            "POST",
            f"{base}/v1/research/jobs",
            {"query": "first", "harness": harness},
            headers,
        )
        assert st1 == 200
        assert body1.get("status") == "running"
        st2, body2 = _http_json(
            "POST",
            f"{base}/v1/research/jobs",
            {"query": "second", "harness": harness},
            headers,
        )
        assert st2 == 200
        assert body2.get("status") == "blocked"
        assert body2.get("phase") == "task_lock_denied"
    finally:
        server.shutdown()
        thread.join(timeout=5)
