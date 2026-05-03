"""Integration test: WorkflowOrchestrator HITL resume cycle."""

from __future__ import annotations

import pytest

from agentium.coordination.workflow_orchestrator import (
    NodeStatus,
    WorkflowNode,
    WorkflowOrchestrator,
    WorkflowSpec,
)
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.models.context import RequestContext
from agentium.shared.errors import ApprovalRequiredError


def test_full_hitl_cycle() -> None:
    audit = InMemoryAuditSink()
    gate = ApprovalGate()
    state_holder = {"approval_id": None}

    def pending_node(ctx, inputs):
        if state_holder["approval_id"] is None:
            request = gate.request_approval(
                context=ctx,
                tool_name="risky",
                reason="needs HITL",
                args_hash="abc",
            )
            state_holder["approval_id"] = request.approval_id
            raise ApprovalRequiredError(
                "needs approval", approval_id=request.approval_id
            )
        return {"approved": True, "id": state_holder["approval_id"]}

    spec = WorkflowSpec(
        name="hitl-wf",
        nodes=[WorkflowNode(name="gate", handler_name="gate")],
    )
    orchestrator = WorkflowOrchestrator(
        handlers={"gate": pending_node}, audit_sink=audit
    )
    context = RequestContext(
        request_id="r",
        run_id="run-hitl",
        tenant_id="tenant-a",
        user_id="user-a",
        trace_id="trace",
    )
    state = orchestrator.run(context=context, spec=spec)
    assert state.pending_node == "gate"
    approval_id = state.pending_approval_id
    assert approval_id is not None
    assert gate.approve(approval_id, approver_id="ops") is True
    state = orchestrator.resume(context=context, spec=spec, approval_id=approval_id)
    assert state.completed_nodes["gate"].status == NodeStatus.COMPLETED
