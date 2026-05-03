from __future__ import annotations

from pathlib import Path

from agentium.api.control_plane import ApprovalDecisionType, ControlPlaneAPI
from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.core.agent_runtime import AgentRuntime
from agentium.core.scheduler import TenantFairScheduler
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.models.context import RequestContext
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


class _TelemetrySpy:
    def __init__(self) -> None:
        self.events = []

    def start_span(self, name, attributes=None):
        del name, attributes
        from contextlib import nullcontext

        return nullcontext()

    def record_tool_execution(self, tool_name, status, latency_ms, attributes):
        del tool_name, status, latency_ms, attributes

    def record_runtime_turn(self, status, error_code, attributes):
        del status, error_code, attributes

    def record_event(self, name, attributes):
        self.events.append((name, attributes))

    def record_quota_hard_limit_trigger(self, attributes):
        del attributes


def _write_policy(tmp_path: Path) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: deny",
                "default_reason: denied by default",
                "rules:",
                "  - id: require-db-approval",
                "    decision: require_approval",
                "    reason: export requires approval",
                "    tools: [db_export]",
                "    roles: [admin]",
            ]
        ),
        encoding="utf-8",
    )
    return path


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


def _build_control_api(tmp_path: Path) -> ControlPlaneAPI:
    policy_engine = PolicyEngine.load(_write_policy(tmp_path))
    ledger = BudgetLedger(
        {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
    )
    gate = ApprovalGate()
    audit_sink = InMemoryAuditSink()
    registry = ToolRegistry(
        policy_engine=policy_engine,
        budget_ledger=ledger,
        audit_sink=audit_sink,
        approval_gate=gate,
    )
    registry.register(
        ToolSpec(
            name="db_export",
            capabilities=["db.export"],
            risk_level="high",
            handler=lambda args: {"ok": True, "dataset": args["dataset"]},
        )
    )
    runtime = AgentRuntime(tool_registry=registry)
    return ControlPlaneAPI(runtime=runtime, approval_service=gate, audit_sink=audit_sink)


def test_control_plane_approval_query_and_resume(tmp_path: Path) -> None:
    api = _build_control_api(tmp_path)
    context = _context()

    pending = api.run_turn(context=context, tool_name="db_export", args={"dataset": "daily"})
    assert pending.status == "pending_approval"
    assert pending.approval_id is not None

    approval_state = api.get_approval(pending.approval_id)
    assert approval_state is not None
    assert approval_state.status == "pending"

    decision = api.decide_approval(
        approval_id=pending.approval_id,
        decision=ApprovalDecisionType.APPROVE,
        approver_id="reviewer-1",
        comment="approved",
    )
    assert decision.applied is True
    assert decision.status == "approved"

    resumed = api.resume_turn(
        context=context,
        tool_name="db_export",
        approval_id=pending.approval_id,
        args={"dataset": "daily"},
    )
    assert resumed.status == "completed"
    assert resumed.output == {"ok": True, "dataset": "daily"}


def test_control_plane_reject_blocks_resume(tmp_path: Path) -> None:
    api = _build_control_api(tmp_path)
    context = _context()

    pending = api.run_turn(context=context, tool_name="db_export", args={"dataset": "daily"})
    assert pending.approval_id is not None

    decision = api.decide_approval(
        approval_id=pending.approval_id,
        decision=ApprovalDecisionType.REJECT,
        approver_id="reviewer-2",
        comment="reject",
    )
    assert decision.applied is True
    assert decision.status == "rejected"

    resumed = api.resume_turn(
        context=context,
        tool_name="db_export",
        approval_id=pending.approval_id,
        args={"dataset": "daily"},
    )
    assert resumed.status == "blocked"
    assert resumed.error_code == "policy_denied"


def test_control_plane_can_query_abac_audit_events(tmp_path: Path) -> None:
    api = _build_control_api(tmp_path)
    context = _context()

    _ = api.run_turn(context=context, tool_name="db_export", args={"dataset": "daily"})
    audit_events = api.get_audit_events(
        run_id="run-1",
        tenant_id="tenant-a",
        event_type="policy_decision",
    )
    assert len(audit_events) >= 1
    assert all(event.event_type == "policy_decision" for event in audit_events)


def test_control_plane_records_hitl_outer_loop_latency(tmp_path: Path) -> None:
    policy_engine = PolicyEngine.load(_write_policy(tmp_path))
    ledger = BudgetLedger(
        {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
    )
    gate = ApprovalGate()
    telemetry = _TelemetrySpy()
    registry = ToolRegistry(
        policy_engine=policy_engine,
        budget_ledger=ledger,
        audit_sink=InMemoryAuditSink(),
        approval_gate=gate,
    )
    registry.register(
        ToolSpec(
            name="db_export",
            capabilities=["db.export"],
            risk_level="high",
            handler=lambda args: {"ok": True},
        )
    )
    api = ControlPlaneAPI(
        runtime=AgentRuntime(tool_registry=registry),
        approval_service=gate,
        telemetry=telemetry,
    )

    pending = api.run_turn(context=_context(), tool_name="db_export", args={"dataset": "daily"})
    assert pending.approval_id is not None
    api.decide_approval(
        approval_id=pending.approval_id,
        decision=ApprovalDecisionType.APPROVE,
        approver_id="reviewer-1",
    )

    name, attrs = telemetry.events[-1]
    assert name == "hitl_outer_loop_decision"
    assert attrs["loop_type"] == "outer_hitl"
    assert attrs["approval_wait_ms"] >= 0


def test_control_plane_records_backpressure_event(tmp_path: Path) -> None:
    api = _build_control_api(tmp_path)
    telemetry = _TelemetrySpy()
    api = ControlPlaneAPI(
        runtime=api._runtime,  # type: ignore[attr-defined]
        approval_service=api._approval_service,  # type: ignore[attr-defined]
        scheduler=TenantFairScheduler(max_queue_per_tenant=0),
        telemetry=telemetry,
    )

    response = api.run_turn(context=_context(), tool_name="db_export", args={"dataset": "daily"})

    assert response.status == "blocked"
    assert response.error_code == "backpressure"
    assert any(name == "control_plane_backpressure" for name, _attrs in telemetry.events)
