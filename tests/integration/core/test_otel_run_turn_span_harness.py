"""Development harness: required OTel span names for one successful run_turn (non-calibrated)."""

from __future__ import annotations

import pytest

from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.core.agent_runtime import AgentRuntime, RuntimeStatus
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.infra.telemetry.otel import OTelTelemetry
from agentium.models.context import RequestContext
from agentium.tools.tool_registry import ToolRegistry, ToolSpec

try:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
except ImportError:  # pragma: no cover
    InMemorySpanExporter = None  # type: ignore[misc,assignment]


REQUIRED_RUN_TURN_SPANS = frozenset({"agentium.turn.run"})


@pytest.mark.integration
@pytest.mark.skipif(InMemorySpanExporter is None, reason="opentelemetry-sdk not available")
def test_run_turn_emits_expected_otel_span_names(tmp_path) -> None:
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
    memory = InMemorySpanExporter()
    telemetry = OTelTelemetry(
        service_name="agentium-span-harness",
        enable_console_export=False,
        otlp_endpoint=None,
        extra_span_exporters=[memory],
    )
    runtime = AgentRuntime(tool_registry=registry, telemetry=telemetry)
    ctx = RequestContext(
        request_id="req-otel-harness",
        run_id="run-otel-harness",
        tenant_id="tenant-a",
        user_id="user-1",
        trace_id="trace-harness",
        role="analyst",
        deployment_mode="prod",
    )
    result = runtime.run_turn(ctx, "echo", {"message": "otel"})
    assert result.status == RuntimeStatus.COMPLETED
    names = {s.name for s in memory.get_finished_spans()}
    assert REQUIRED_RUN_TURN_SPANS <= names, names
