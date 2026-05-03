from __future__ import annotations

from typing import Any, Dict, Optional

from agentium.api.control_plane import ControlPlaneAPI
from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.core.agent_lifecycle import AgentLifecycleManager, AgentState
from agentium.core.agent_runtime import AgentRuntime
from agentium.core.scheduler import TenantFairScheduler
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.models.context import RequestContext
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


class _TelemetrySpy:
    def __init__(self) -> None:
        self.runtime_calls: list[tuple[str, Optional[str], Dict[str, Any]]] = []
        self.events: list[tuple[str, Dict[str, Any]]] = []

    def start_span(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        del name, attributes
        from contextlib import nullcontext

        return nullcontext()

    def record_tool_execution(
        self, tool_name: str, status: str, latency_ms: int, attributes: Dict[str, Any]
    ) -> None:
        del tool_name, status, latency_ms, attributes

    def record_runtime_turn(
        self, status: str, error_code: Optional[str], attributes: Dict[str, Any]
    ) -> None:
        self.runtime_calls.append((status, error_code, attributes))

    def record_event(self, name: str, attributes: Dict[str, Any]) -> None:
        self.events.append((name, attributes))

    def record_quota_hard_limit_trigger(self, attributes: Dict[str, Any]) -> None:
        del attributes


def _context(run_id: str = "run-a") -> RequestContext:
    return RequestContext(
        request_id=f"req-{run_id}",
        run_id=run_id,
        tenant_id="tenant-a",
        user_id="user-a",
        trace_id=f"trace-{run_id}",
        role="analyst",
    )


def _policy(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "version: appendix-a",
                "default_decision: deny",
                "default_reason: denied by default",
                "rules:",
                "  - id: allow-echo",
                "    decision: allow",
                "    reason: echo allowed",
                "    tools: [echo]",
                "    roles: [analyst]",
            ]
        ),
        encoding="utf-8",
    )
    return PolicyEngine.load(policy_path)


def test_control_plane_runs_turn_through_scheduler_and_lifecycle(tmp_path) -> None:
    telemetry = _TelemetrySpy()
    lifecycle = AgentLifecycleManager()
    registry = ToolRegistry(
        policy_engine=_policy(tmp_path),
        budget_ledger=BudgetLedger(
            {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=2)}
        ),
        audit_sink=InMemoryAuditSink(),
        telemetry=telemetry,
    )
    registry.register(
        ToolSpec(
            name="echo",
            capabilities=["read_only"],
            risk_level="low",
            handler=lambda args: {"echo": args},
        )
    )
    runtime = AgentRuntime(
        tool_registry=registry,
        telemetry=telemetry,
        lifecycle_manager=lifecycle,
    )
    api = ControlPlaneAPI(
        runtime=runtime,
        approval_service=ApprovalGate(),
        audit_sink=InMemoryAuditSink(),
        scheduler=TenantFairScheduler(max_concurrency_per_tenant=1, global_max_concurrency=1),
    )

    response = api.run_turn(context=_context(), tool_name="echo", args={"x": 1})

    assert response.status == "completed"
    assert lifecycle.get("run-a").state == AgentState.CLEANED
    assert telemetry.runtime_calls[-1][2]["lifecycle_state"] == "cleaned"
    assert telemetry.runtime_calls[-1][2]["scheduler_queue_wait_ms"] >= 0


def test_control_plane_maps_backpressure_to_blocked_response(tmp_path) -> None:
    registry = ToolRegistry(
        policy_engine=_policy(tmp_path),
        budget_ledger=BudgetLedger(
            {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=2)}
        ),
        audit_sink=InMemoryAuditSink(),
    )
    runtime = AgentRuntime(tool_registry=registry)
    scheduler = TenantFairScheduler(
        max_concurrency_per_tenant=1,
        global_max_concurrency=1,
        max_queue_per_tenant=0,
    )
    api = ControlPlaneAPI(
        runtime=runtime,
        approval_service=ApprovalGate(),
        audit_sink=InMemoryAuditSink(),
        scheduler=scheduler,
    )

    response = api.run_turn(context=_context("run-backpressure"), tool_name="echo", args={})

    assert response.status == "blocked"
    assert response.error_code == "backpressure"
