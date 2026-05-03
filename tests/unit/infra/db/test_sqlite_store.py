from __future__ import annotations

from pathlib import Path

from agentium.coordination.budget_ledger import TenantBudget
from agentium.infra.db.sqlite_store import SqliteApprovalGate, SqliteAuditSink
from agentium.models.context import AuditRecord, RequestContext
from agentium.infra.db.sqlite_store import SqliteBudgetLedger


def _context() -> RequestContext:
    return RequestContext(
        request_id="req-1",
        run_id="run-1",
        tenant_id="tenant-a",
        user_id="user-1",
        trace_id="trace-1",
        role="admin",
        deployment_mode="prod",
    )


def test_sqlite_audit_sink_persists_records(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "agentium.db"
    sink = SqliteAuditSink(db_path)
    sink.append(
        AuditRecord(
            event_type="tool_executed",
            tenant_id="tenant-a",
            run_id="run-1",
            payload={"tool_name": "echo"},
        )
    )
    sink.close()

    reopened = SqliteAuditSink(db_path)
    records = reopened.query(run_id="run-1")
    reopened.close()

    assert len(records) == 1
    assert records[0].payload["tool_name"] == "echo"


def test_sqlite_approval_gate_persists_status(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "agentium.db"
    gate = SqliteApprovalGate(db_path)
    request = gate.request_approval(
        context=_context(),
        tool_name="db_export",
        reason="high risk",
        args_hash="hash-1",
    )
    approved = gate.approve(request.approval_id, approver_id="reviewer-1")
    assert approved is True
    gate.close()

    reopened = SqliteApprovalGate(db_path)
    loaded = reopened.get_request(request.approval_id)
    reopened.close()

    assert loaded is not None
    assert loaded.status.value == "approved"
    assert loaded.approver_id == "reviewer-1"


def test_sqlite_budget_ledger_reserve_commit_and_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "agentium.db"
    ledger = SqliteBudgetLedger(
        db_path,
        tenant_budgets={
            "tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)
        },
    )
    context = _context()

    assert ledger.reserve(context, estimated_tokens=100, estimated_cost=1.0) is True
    ledger.commit(context, actual_tokens=120, actual_cost=1.5)
    usage = ledger.usage_for_tenant("tenant-a")
    assert usage is not None
    assert usage.tokens_used == 120
    assert usage.inflight_calls == 0
    ledger.close()

    reopened = SqliteBudgetLedger(
        db_path,
        tenant_budgets={
            "tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)
        },
    )
    usage_after_reopen = reopened.usage_for_tenant("tenant-a")
    reopened.close()
    assert usage_after_reopen is not None
    assert usage_after_reopen.tokens_used == 120
