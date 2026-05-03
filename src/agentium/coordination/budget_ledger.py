"""Budget and quota ledger for tenant-aware runtime control."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Dict, Optional, Tuple

from typing_extensions import Protocol

from agentium.models.context import RequestContext


@dataclass(frozen=True)
class TenantBudget:
    """Budget and quota constraints for one tenant."""

    token_limit: int
    cost_limit: float
    max_concurrency: int
    soft_memory_mb: Optional[int] = None
    hard_memory_mb: Optional[int] = None
    soft_cpu_millis: Optional[int] = None
    hard_cpu_millis: Optional[int] = None
    soft_tool_slots: Optional[int] = None
    hard_tool_slots: Optional[int] = None
    soft_outbound_rps: Optional[float] = None
    hard_outbound_rps: Optional[float] = None
    degrade_order: Tuple[str, ...] = ("reduce_context", "disable_optional_tools")


class LimitAction(str, Enum):
    """Action selected when a resource limit is crossed."""

    ALLOW = "allow"
    DEGRADE = "degrade"
    REJECT = "reject"
    OOM_KILL = "oom_kill"


@dataclass(frozen=True)
class ResourceDemand:
    """Resource demand estimate for one runtime operation."""

    memory_mb: Optional[int] = None
    cpu_millis: Optional[int] = None
    tool_slots: Optional[int] = None
    outbound_rps: Optional[float] = None


@dataclass(frozen=True)
class ResourceDecision:
    """Decision returned by cgroups-style resource evaluation."""

    allowed: bool
    action: LimitAction
    degraded: bool = False
    limit_name: Optional[str] = None
    observed_value: Optional[float] = None
    limit_value: Optional[float] = None
    degrade_steps: Tuple[str, ...] = ()


class ResourceLimitController:
    """Evaluate soft/hard tenant resource limits before execution."""

    def __init__(
        self,
        tenant_budgets: Dict[str, TenantBudget],
        default_budget: Optional[TenantBudget] = None,
    ) -> None:
        self._tenant_budgets = tenant_budgets
        self._default_budget = default_budget

    def evaluate(
        self, context: RequestContext, demand: ResourceDemand
    ) -> ResourceDecision:
        """Evaluate a resource demand against tenant hard/soft limits."""

        budget = self._tenant_budgets.get(context.tenant_id) or self._default_budget
        if budget is None:
            return ResourceDecision(
                allowed=False,
                action=LimitAction.REJECT,
                limit_name="tenant_budget",
            )
        hard = self._first_exceeded(
            demand=demand,
            limits={
                "memory_mb": budget.hard_memory_mb,
                "cpu_millis": budget.hard_cpu_millis,
                "tool_slots": budget.hard_tool_slots,
                "outbound_rps": budget.hard_outbound_rps,
            },
        )
        if hard is not None:
            name, observed, limit = hard
            return ResourceDecision(
                allowed=False,
                action=LimitAction.OOM_KILL if name == "memory_mb" else LimitAction.REJECT,
                limit_name=name,
                observed_value=observed,
                limit_value=limit,
            )
        soft = self._first_exceeded(
            demand=demand,
            limits={
                "memory_mb": budget.soft_memory_mb,
                "cpu_millis": budget.soft_cpu_millis,
                "tool_slots": budget.soft_tool_slots,
                "outbound_rps": budget.soft_outbound_rps,
            },
        )
        if soft is not None:
            name, observed, limit = soft
            return ResourceDecision(
                allowed=True,
                action=LimitAction.DEGRADE,
                degraded=True,
                limit_name=name,
                observed_value=observed,
                limit_value=limit,
                degrade_steps=budget.degrade_order,
            )
        return ResourceDecision(allowed=True, action=LimitAction.ALLOW)

    @staticmethod
    def _first_exceeded(
        demand: ResourceDemand, limits: Dict[str, Optional[float]]
    ) -> Optional[tuple[str, float, float]]:
        values = {
            "memory_mb": demand.memory_mb,
            "cpu_millis": demand.cpu_millis,
            "tool_slots": demand.tool_slots,
            "outbound_rps": demand.outbound_rps,
        }
        for name, limit in limits.items():
            observed = values[name]
            if observed is not None and limit is not None and float(observed) > float(limit):
                return name, float(observed), float(limit)
        return None


@dataclass
class BudgetUsage:
    """Mutable usage counters per tenant."""

    tokens_used: int = 0
    cost_used: float = 0.0
    inflight_calls: int = 0


@dataclass
class _Reservation:
    """Reservation counters per run."""

    tenant_id: str
    estimated_tokens: int
    estimated_cost: float


class BudgetService(Protocol):
    """Protocol for budget reservation/accounting backends."""

    def reserve(
        self, context: RequestContext, estimated_tokens: int, estimated_cost: float
    ) -> bool:
        """Reserve budget for one operation."""

    def commit(self, context: RequestContext, actual_tokens: int, actual_cost: float) -> None:
        """Commit consumed budget for one operation."""

    def release(self, context: RequestContext) -> None:
        """Release reserved budget without committing."""

    def usage_for_tenant(self, tenant_id: str) -> Optional[BudgetUsage]:
        """Return current usage snapshot for one tenant."""


class BudgetLedger:
    """Thread-safe budget reservation and accounting service."""

    def __init__(
        self,
        tenant_budgets: Dict[str, TenantBudget],
        default_budget: Optional[TenantBudget] = None,
    ) -> None:
        self._tenant_budgets = tenant_budgets
        self._default_budget = default_budget
        self._tenant_usage: Dict[str, BudgetUsage] = {}
        self._reservations_by_run: Dict[str, _Reservation] = {}
        self._lock = Lock()

    def reserve(
        self, context: RequestContext, estimated_tokens: int, estimated_cost: float
    ) -> bool:
        """Try to reserve quota for one runtime operation.

        Args:
            context: Current request context.
            estimated_tokens: Planned token usage for operation.
            estimated_cost: Planned monetary cost for operation.
        """

        if estimated_tokens < 0 or estimated_cost < 0:
            return False
        with self._lock:
            budget = self._tenant_budgets.get(context.tenant_id)
            if budget is None:
                budget = self._default_budget
            if budget is None:
                return False
            usage = self._tenant_usage.setdefault(context.tenant_id, BudgetUsage())
            if usage.inflight_calls >= budget.max_concurrency:
                return False
            projected_tokens = usage.tokens_used + estimated_tokens
            projected_cost = usage.cost_used + estimated_cost
            if projected_tokens > budget.token_limit:
                return False
            if projected_cost > budget.cost_limit:
                return False
            usage.tokens_used = projected_tokens
            usage.cost_used = projected_cost
            usage.inflight_calls += 1
            self._reservations_by_run[context.run_id] = _Reservation(
                tenant_id=context.tenant_id,
                estimated_tokens=estimated_tokens,
                estimated_cost=estimated_cost,
            )
            return True

    def commit(
        self, context: RequestContext, actual_tokens: int, actual_cost: float
    ) -> None:
        """Finalize budget usage and remove reservation delta.

        Args:
            context: Current request context.
            actual_tokens: Actual consumed tokens.
            actual_cost: Actual consumed cost.
        """

        with self._lock:
            reservation = self._reservations_by_run.get(context.run_id)
            if reservation is None:
                return
            usage = self._tenant_usage.get(reservation.tenant_id)
            if usage is None:
                return
            usage.tokens_used += max(0, actual_tokens - reservation.estimated_tokens)
            usage.cost_used += max(0.0, actual_cost - reservation.estimated_cost)
            usage.inflight_calls = max(0, usage.inflight_calls - 1)
            del self._reservations_by_run[context.run_id]

    def release(self, context: RequestContext) -> None:
        """Release reserved quota without committing usage."""

        with self._lock:
            reservation = self._reservations_by_run.get(context.run_id)
            if reservation is None:
                return
            usage = self._tenant_usage.get(reservation.tenant_id)
            if usage is not None:
                usage.tokens_used = max(0, usage.tokens_used - reservation.estimated_tokens)
                usage.cost_used = max(0.0, usage.cost_used - reservation.estimated_cost)
                usage.inflight_calls = max(0, usage.inflight_calls - 1)
            del self._reservations_by_run[context.run_id]

    def usage_for_tenant(self, tenant_id: str) -> Optional[BudgetUsage]:
        """Return copy-like usage snapshot for one tenant."""

        with self._lock:
            usage = self._tenant_usage.get(tenant_id)
            if usage is None:
                return None
            return BudgetUsage(
                tokens_used=usage.tokens_used,
                cost_used=usage.cost_used,
                inflight_calls=usage.inflight_calls,
            )

    def tenant_budget_summary(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Read-only limits + usage for HTTP /v1/budget/.../summary."""

        with self._lock:
            budget = self._tenant_budgets.get(tenant_id)
            if budget is None:
                budget = self._default_budget
            if budget is None:
                return None
            usage = self._tenant_usage.get(tenant_id)
        return {
            "tenant_id": tenant_id,
            "limits": {
                "token_limit": budget.token_limit,
                "cost_limit": budget.cost_limit,
                "max_concurrency": budget.max_concurrency,
            },
            "usage": None
            if usage is None
            else {
                "tokens_used": usage.tokens_used,
                "cost_used": usage.cost_used,
                "inflight_calls": usage.inflight_calls,
            },
        }
