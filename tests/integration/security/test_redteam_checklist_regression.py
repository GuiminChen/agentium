"""One repeatable case per section of docs/security-ops/security-acceptance-checklist.md (§1–§4).

Run via ``python scripts/redteam_regression.py`` (bundle includes this module).
"""

from __future__ import annotations

import pytest

from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyDocument, PolicyEngine, PolicyRule
from agentium.infra.db.sqlite_store import SqliteRunMessageStore
from agentium.models.context import DecisionType, RequestContext
from agentium.security.dlp_classifier import DLPClassifier
from agentium.shared.errors import PolicyDeniedError
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


def _allow_tool_engine(tool_name: str) -> PolicyEngine:
    return PolicyEngine(
        policy=PolicyDocument(
            version="t",
            default_decision=DecisionType.DENY,
            default_reason="default",
            rules=[
                PolicyRule(
                    id="allow",
                    decision=DecisionType.ALLOW,
                    reason="allow",
                    tools=[tool_name],
                )
            ],
        )
    )


@pytest.mark.integration
def test_section1_manifest_undeclared_tool_is_denied_with_audit(tmp_path) -> None:
    """§1: undeclared tool vs run_manifest declared allowlist → deny + audit."""

    def _policy_manifest(tmp_path_inner) -> PolicyEngine:
        path = tmp_path_inner / "redteam-pol.yaml"
        path.write_text(
            "\n".join(
                [
                    "version: p0",
                    "default_decision: deny",
                    "default_reason: denied by default",
                    "rules:",
                    "  - id: allow-rogue",
                    "    decision: allow",
                    "    reason: ok",
                    "    tools: [rogue_tool]",
                    "    roles: [analyst]",
                ]
            ),
            encoding="utf-8",
        )
        return PolicyEngine.load(path)

    audit = InMemoryAuditSink()
    registry = ToolRegistry(
        policy_engine=_policy_manifest(tmp_path),
        budget_ledger=BudgetLedger(
            {"t1": TenantBudget(token_limit=5000, cost_limit=50.0, max_concurrency=2)}
        ),
        audit_sink=audit,
        approval_gate=ApprovalGate(),
    )
    registry.register(
        ToolSpec(name="rogue_tool", capabilities=[], risk_level="high", handler=lambda a: {"ok": True})
    )
    ctx = RequestContext(
        request_id="r-s1",
        run_id="run-s1",
        tenant_id="t1",
        user_id="u1",
        trace_id="tr-s1",
        role="analyst",
        manifest_declared_tools=["allowed_only"],
        run_manifest_sha256="deadbeef" * 8,
    )
    with pytest.raises(PolicyDeniedError):
        registry.execute(context=ctx, name="rogue_tool", args={})
    types = {e.event_type for e in audit.query(run_id="run-s1")}
    assert "run_manifest_tool_denied" in types


@pytest.mark.integration
def test_section2_dlp_blocks_ssh_key_material_in_tool_output() -> None:
    """§2: outbound DLP blocks representative secret-shaped tool output."""

    audit = InMemoryAuditSink()
    registry = ToolRegistry(
        policy_engine=_allow_tool_engine("leak"),
        budget_ledger=BudgetLedger(
            {"t1": TenantBudget(token_limit=10000, cost_limit=10.0, max_concurrency=4)}
        ),
        audit_sink=audit,
        approval_gate=ApprovalGate(),
        dlp_classifier=DLPClassifier(),
    )
    registry.register(
        ToolSpec(
            name="leak",
            capabilities=["read_only"],
            risk_level="low",
            handler=lambda args: {
                "body": "-----BEGIN OPENSSH PRIVATE KEY-----\nABC\n-----END OPENSSH PRIVATE KEY-----"
            },
        )
    )
    context = RequestContext(
        request_id="r-s2",
        run_id="run-s2",
        tenant_id="t1",
        user_id="u1",
        trace_id="trace-s2",
    )
    with pytest.raises(PolicyDeniedError):
        registry.execute(context=context, name="leak", args={})
    assert any(e.event_type == "dlp_blocked" for e in audit.query())


@pytest.mark.integration
def test_section3_sqlite_session_messages_isolated_per_tenant(tmp_path) -> None:
    """§3: cross-tenant read of session timeline data returns nothing (no leakage)."""

    db = tmp_path / "iso.db"
    store = SqliteRunMessageStore(db)
    try:
        store.append(
            run_id="run-x",
            tenant_id="tenant-a",
            role="user",
            kind="turn_request",
            body={"text": "secret"},
            request_id="req-a",
        )
        page_other, _ = store.list_page(run_id="run-x", tenant_id="tenant-b", after_seq=0, limit=50)
        assert page_other == []
    finally:
        store.close()


@pytest.mark.integration
def test_section4_background_noise_tripwire_pauses_daemon(tmp_path) -> None:
    """§4: burst ingest rate triggers tripwire audit and pause (see also integration/background)."""

    from agentium.background.background_daemon import BackgroundDaemon
    from agentium.background.event_ingestor import EventIngestor
    from agentium.background.trigger_planner import TriggerPlanner

    policy_engine = PolicyEngine(
        policy=PolicyDocument(
            version="noise",
            default_decision=DecisionType.DENY,
            default_reason="default",
            rules=[],
        )
    )
    ing = EventIngestor(clock=lambda: 999999.0, rate_window_seconds=1.0)
    planner = TriggerPlanner(rules=[])
    sink = InMemoryAuditSink()
    daemon = BackgroundDaemon(
        approval_service=ApprovalGate(),
        audit_sink=sink,
        policy_engine=policy_engine,
        interval_seconds=60.0,
        event_ingestor=ing,
        trigger_planner=planner,
        noise_rps_pause=12.0,
    )
    for i in range(20):
        ing.submit("noise.redteam", {"i": i}, dedupe_key=str(i))
    daemon.tick_full()
    assert daemon.paused
    assert any(e.event_type == "background_noise_tripwire" for e in sink.query())
