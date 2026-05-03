from __future__ import annotations

from agentium.coordination.budget_ledger import (
    LimitAction,
    ResourceDemand,
    ResourceLimitController,
    TenantBudget,
)
from agentium.models.context import RequestContext


def _context(run_id: str = "run-res-1") -> RequestContext:
    return RequestContext(
        request_id="req-res-1",
        run_id=run_id,
        tenant_id="tenant-a",
        user_id="user-a",
        trace_id="trace-res-1",
    )


def test_resource_limit_soft_excess_allows_with_degradation() -> None:
    controller = ResourceLimitController(
        tenant_budgets={
            "tenant-a": TenantBudget(
                token_limit=1000,
                cost_limit=10.0,
                max_concurrency=2,
                soft_memory_mb=128,
                hard_memory_mb=256,
                degrade_order=("reduce_context", "disable_optional_tools"),
            )
        }
    )

    decision = controller.evaluate(_context(), ResourceDemand(memory_mb=192))

    assert decision.allowed is True
    assert decision.degraded is True
    assert decision.action == LimitAction.DEGRADE
    assert decision.degrade_steps == ("reduce_context", "disable_optional_tools")


def test_resource_limit_hard_memory_excess_requests_oom_kill() -> None:
    controller = ResourceLimitController(
        tenant_budgets={
            "tenant-a": TenantBudget(
                token_limit=1000,
                cost_limit=10.0,
                max_concurrency=2,
                hard_memory_mb=256,
            )
        }
    )

    decision = controller.evaluate(_context(), ResourceDemand(memory_mb=300))

    assert decision.allowed is False
    assert decision.action == LimitAction.OOM_KILL
    assert decision.limit_name == "memory_mb"


def test_resource_limit_hard_outbound_excess_rejects() -> None:
    controller = ResourceLimitController(
        tenant_budgets={
            "tenant-a": TenantBudget(
                token_limit=1000,
                cost_limit=10.0,
                max_concurrency=2,
                hard_outbound_rps=5.0,
            )
        }
    )

    decision = controller.evaluate(_context(), ResourceDemand(outbound_rps=6.0))

    assert decision.allowed is False
    assert decision.action == LimitAction.REJECT
    assert decision.limit_name == "outbound_rps"
