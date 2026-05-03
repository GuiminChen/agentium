from __future__ import annotations

from typing import Any, Dict, Optional

from agentium.core.agent_runtime import AgentRuntime, RuntimeStatus
from agentium.models.context import RequestContext
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


class _TelemetrySpy:
    def __init__(self) -> None:
        self.runtime_calls = []

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
        del name, attributes

    def record_quota_hard_limit_trigger(self, attributes: Dict[str, Any]) -> None:
        del attributes


def _context() -> RequestContext:
    return RequestContext(
        request_id="req-tele-1",
        run_id="run-tele-1",
        tenant_id="tenant-a",
        user_id="user-1",
        trace_id="trace-1",
        role="analyst",
        deployment_mode="prod",
    )


def test_agent_runtime_records_telemetry_status(tmp_path) -> None:
    from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
    from agentium.governance.audit_lineage import InMemoryAuditSink
    from agentium.governance.policy_engine import PolicyEngine

    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "version: p0",
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
    engine = PolicyEngine.load(policy_path)
    registry = ToolRegistry(
        policy_engine=engine,
        budget_ledger=BudgetLedger(
            {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
        ),
        audit_sink=InMemoryAuditSink(),
    )
    registry.register(
        ToolSpec(
            name="echo",
            capabilities=["utility"],
            risk_level="low",
            handler=lambda args: {"message": args["message"]},
        )
    )
    telemetry = _TelemetrySpy()
    runtime = AgentRuntime(tool_registry=registry, telemetry=telemetry)

    result = runtime.run_turn(_context(), "echo", {"message": "hello"})

    assert result.status == RuntimeStatus.COMPLETED
    assert telemetry.runtime_calls
    status, error_code, attributes = telemetry.runtime_calls[-1]
    assert status == "completed"
    assert error_code is None
    assert attributes["turn_type"] == "run_turn"
