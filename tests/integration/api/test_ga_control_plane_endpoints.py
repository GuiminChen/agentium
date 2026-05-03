"""GA control-plane endpoints: sessions timeline, eval persist/compare, cancel, domain pack, inbox."""

from __future__ import annotations

import json
import threading
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib import error, request

import pytest

from agentium.api.control_plane import ControlPlaneAPI
from agentium.api.http.resources import HTTPControlPlaneResources
from agentium.api.http_control_plane import build_http_server
from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.core.agent_runtime import AgentRuntime
from agentium.core.run_cancellation import RunCancelRegistry
from agentium.governance.policy_engine import PolicyEngine
from agentium.infra.db.sqlite_store import (
    SqliteApprovalGate,
    SqliteAuditSink,
    SqliteEvalRunStore,
    SqliteRunMessageStore,
)
from agentium.models.context import AuditRecord
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


def _http_raw(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, bytes, Dict[str, str]]:
    req = request.Request(url, method=method)
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)
    with request.urlopen(req, timeout=30) as resp:
        data = resp.read()
        hdrs = {k.lower(): v for k, v in resp.headers.items()}
        return resp.getcode(), data, hdrs


def _build_ga_stack(
    tmp_path: Path, packs_root: Path
) -> Tuple[
    ControlPlaneAPI,
    SqliteAuditSink,
    SqliteApprovalGate,
    SqliteRunMessageStore,
    SqliteEvalRunStore,
    threading.Thread,
    Any,
]:
    db_path = tmp_path / "ga.db"
    audit = SqliteAuditSink(db_path)
    gate = SqliteApprovalGate(db_path)
    msg_store = SqliteRunMessageStore(db_path)
    eval_store = SqliteEvalRunStore(db_path)
    cancel_reg = RunCancelRegistry()
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
    runtime = AgentRuntime(
        tool_registry=registry,
        run_cancel_registry=cancel_reg,
    )
    api = ControlPlaneAPI(runtime=runtime, approval_service=gate, audit_sink=audit)
    resources = HTTPControlPlaneResources(
        run_message_store=msg_store,
        eval_run_store=eval_store,
        run_cancel_registry=cancel_reg,
        sqlite_audit_sink=audit,
        domain_packs_root=packs_root,
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
    return api, audit, gate, msg_store, eval_store, thread, server


@pytest.mark.integration
def test_ga_sessions_messages_eval_cancel_pack_inbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    packs = tmp_path / "packs"
    packs.mkdir(parents=True)
    pack_dir = packs / "demo-pack"
    pack_dir.mkdir()
    pack_dir.joinpath("domain_pack.yaml").write_text(
        "\n".join(
            [
                "id: demo-pack",
                'version: "1.0.0"',
                "signer: integration-test",
                "",
            ]
        ),
        encoding="utf-8",
    )

    _, audit, gate, msg_store, eval_store, thread, server = _build_ga_stack(tmp_path, packs)
    host, port = server.server_address
    base = f"http://{host}:{port}"
    headers = {
        "X-Tenant-Id": "t1",
        "X-User-Id": "u1",
        "X-Role": "admin",
    }
    run_id = "ga-run-1"

    _gate_summaries = iter(
        [
            {
                "passed": True,
                "started_at": 0.0,
                "finished_at": 1.0,
                "results": [
                    {
                        "name": "stub_gate",
                        "passed": True,
                        "duration_ms": 1,
                        "detail": {},
                        "error": "",
                    }
                ],
            },
            {
                "passed": False,
                "started_at": 0.0,
                "finished_at": 1.0,
                "results": [
                    {
                        "name": "stub_gate",
                        "passed": False,
                        "duration_ms": 2,
                        "detail": {},
                        "error": "",
                    }
                ],
            },
        ]
    )

    def fake_collect() -> Dict[str, Any]:
        return next(_gate_summaries)

    monkeypatch.setattr(
        "agentium.api.http.handlers_research_eval_workflow.collect_release_gate_summary",
        fake_collect,
    )

    try:
        st_turn, body_turn = _http_json(
            "POST",
            base + "/v1/turn",
            headers=headers,
            payload={
                "tool_name": "noop",
                "args": {},
                "run_id": run_id,
                "request_id": "req-ga-1",
                "trace_id": "tr-ga-1",
                "message_disposition": "collect",
                "mcp_execution_tier": "direct-tool",
            },
        )
        assert st_turn == 200
        assert body_turn["status"] == "completed"

        st_msg, body_msg = _http_json(
            "GET",
            base + f"/v1/sessions/{run_id}/messages?limit=20",
            headers=headers,
        )
        assert st_msg == 200
        assert body_msg["count"] >= 1
        kinds = {m["kind"] for m in body_msg["messages"]}
        assert "turn_request" in kinds
        assert "turn_result" in kinds

        st_eval1, body_eval1 = _http_json("POST", base + "/v1/eval/gates", headers=headers)
        assert st_eval1 == 200
        eval_id_a = body_eval1["eval_id"]
        assert eval_id_a

        st_eval2, body_eval2 = _http_json("POST", base + "/v1/eval/gates", headers=headers)
        assert st_eval2 == 200
        eval_id_b = body_eval2["eval_id"]
        assert eval_id_b != eval_id_a

        st_list, body_list = _http_json("GET", base + "/v1/eval/runs?limit=10", headers=headers)
        assert st_list == 200
        assert body_list["count"] >= 2

        st_cmp, body_cmp = _http_json(
            "POST",
            base + "/v1/eval/compare",
            headers=headers,
            payload={"baseline_eval_id": eval_id_a, "candidate_eval_id": eval_id_b},
        )
        assert st_cmp == 200
        assert "diff" in body_cmp
        assert body_cmp["diff"]["changed"]

        st_cancel, body_cancel = _http_json(
            "POST",
            base + f"/v1/runs/{run_id}/cancel",
            headers=headers,
        )
        assert st_cancel == 200
        assert body_cancel["cancelled"] is True

        st_blocked, body_blocked = _http_json(
            "POST",
            base + "/v1/turn",
            headers=headers,
            payload={
                "tool_name": "noop",
                "args": {},
                "run_id": run_id,
                "request_id": "req-ga-2",
                "trace_id": "tr-ga-2",
                "message_disposition": "collect",
                "mcp_execution_tier": "direct-tool",
            },
        )
        assert st_blocked == 200
        assert body_blocked["status"] == "blocked"
        assert body_blocked.get("error_code") == "cancelled"

        st_zip, raw_zip, hdrs = _http_raw(
            "GET",
            base + "/v1/governance/domain-packs/demo-pack/bundle",
            headers=headers,
        )
        assert st_zip == 200
        assert raw_zip[:2] == b"PK"
        assert "x-agentium-pack-id" in hdrs

        buf = BytesIO(raw_zip)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert "domain_pack.yaml" in names

        audit.append(
            AuditRecord(
                event_type="channel_delivered",
                tenant_id="t1",
                run_id="inbox-demo",
                payload={"channel": "slack", "reason": "integration_fixture"},
            )
        )

        st_inbox, body_inbox = _http_json("GET", base + "/v1/connectors/inbox?limit=10", headers=headers)
        assert st_inbox == 200
        assert body_inbox["count"] >= 1
        assert any(e.get("channel") == "slack" for e in body_inbox["events"])

        st_nf, body_nf = _http_json(
            "GET",
            base + "/v1/governance/domain-packs/missing-pack/bundle",
            headers=headers,
        )
        assert st_nf == 404
        assert body_nf.get("error") == "pack_not_found"

    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        audit.close()
        gate.close()
        msg_store.close()
        eval_store.close()
