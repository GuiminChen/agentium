from __future__ import annotations

from typing import Any, Dict, Optional

from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.core.agent_runtime import AgentRuntime, RuntimeStatus
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.models.context import RequestContext
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


class _TelemetrySpy:
    def __init__(self) -> None:
        self.runtime_calls = []
        self.events = []

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


def _context() -> RequestContext:
    return RequestContext(
        request_id="req-reliability-1",
        run_id="run-reliability-1",
        tenant_id="tenant-a",
        user_id="user-1",
        trace_id="trace-1",
        role="admin",
        deployment_mode="prod",
    )


def test_runtime_maps_unexpected_tool_error_to_internal_error(tmp_path) -> None:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: deny",
                "default_reason: denied by default",
                "rules:",
                "  - id: allow-flaky",
                "    decision: allow",
                "    reason: flaky allowed",
                "    tools: [flaky_tool]",
                "    roles: [admin]",
            ]
        ),
        encoding="utf-8",
    )
    registry = ToolRegistry(
        policy_engine=PolicyEngine.load(policy_path),
        budget_ledger=BudgetLedger(
            {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
        ),
        audit_sink=InMemoryAuditSink(),
    )
    registry.register(
        ToolSpec(
            name="flaky_tool",
            capabilities=["unstable"],
            risk_level="medium",
            handler=lambda args: (_ for _ in ()).throw(RuntimeError("boom")),
        )
    )
    telemetry = _TelemetrySpy()
    runtime = AgentRuntime(tool_registry=registry, telemetry=telemetry)

    result = runtime.run_turn(_context(), "flaky_tool", {})

    assert result.status == RuntimeStatus.BLOCKED
    assert result.error_code == "internal_error"
    assert "safe_degrade" in (result.message or "")
    assert telemetry.runtime_calls[-1][1] == "internal_error"
    assert any(name == "runtime_safe_degrade" for name, _attrs in telemetry.events)
