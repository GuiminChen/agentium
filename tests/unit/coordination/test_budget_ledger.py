from __future__ import annotations

from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.models.context import RequestContext


def _context(run_id: str = "run-1") -> RequestContext:
    return RequestContext(
        request_id="req-1",
        run_id=run_id,
        tenant_id="tenant-a",
        user_id="user-1",
        trace_id="trace-1",
        role="analyst",
        deployment_mode="prod",
    )


def test_budget_reserve_and_commit() -> None:
    ledger = BudgetLedger(
        tenant_budgets={
            "tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=2)
        }
    )
    context = _context()

    assert ledger.reserve(context, estimated_tokens=100, estimated_cost=1.0) is True
    ledger.commit(context, actual_tokens=120, actual_cost=1.2)
    usage = ledger.usage_for_tenant("tenant-a")

    assert usage is not None
    assert usage.tokens_used == 120
    assert usage.cost_used == 1.2
    assert usage.inflight_calls == 0


def test_budget_reserve_rejected_when_exceeds_limits() -> None:
    ledger = BudgetLedger(
        tenant_budgets={
            "tenant-a": TenantBudget(token_limit=100, cost_limit=1.0, max_concurrency=1)
        }
    )
    context = _context()

    assert ledger.reserve(context, estimated_tokens=101, estimated_cost=0.5) is False


def test_budget_release_reclaims_reservation() -> None:
    ledger = BudgetLedger(
        tenant_budgets={
            "tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)
        }
    )
    first_context = _context(run_id="run-1")
    second_context = _context(run_id="run-2")

    assert ledger.reserve(first_context, estimated_tokens=200, estimated_cost=2.0) is True
    assert ledger.reserve(second_context, estimated_tokens=100, estimated_cost=1.0) is False

    ledger.release(first_context)

    assert ledger.reserve(second_context, estimated_tokens=100, estimated_cost=1.0) is True
