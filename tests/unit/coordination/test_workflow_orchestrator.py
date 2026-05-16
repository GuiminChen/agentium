"""Unit tests for WorkflowOrchestrator."""

from __future__ import annotations

import pytest

from agentium.coordination.artifact_contract import ArtifactSpec
from agentium.coordination.workflow_orchestrator import (
    NodeStatus,
    WorkflowNode,
    WorkflowOrchestrator,
    WorkflowSpec,
)
from agentium.coordination.task_graph import OrphanPolicy, TaskGraphSupervisor
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.models.context import RequestContext
from agentium.shared.errors import ApprovalRequiredError


@pytest.fixture()
def context() -> RequestContext:
    return RequestContext(
        request_id="r1",
        run_id="run-1",
        tenant_id="tenant-a",
        user_id="user-a",
        trace_id="trace-1",
    )


def test_workflow_runs_in_dependency_order(context: RequestContext) -> None:
    sink = InMemoryAuditSink()

    def node_one(ctx, inputs):
        return {"value": 1}

    def node_two(ctx, inputs):
        return {"value": inputs["one"]["value"] + 1}

    spec = WorkflowSpec(
        name="wf",
        nodes=[
            WorkflowNode(name="one", handler_name="one"),
            WorkflowNode(name="two", handler_name="two", depends_on=["one"]),
        ],
    )
    orchestrator = WorkflowOrchestrator(
        handlers={"one": node_one, "two": node_two},
        audit_sink=sink,
    )
    state = orchestrator.run(context=context, spec=spec)
    assert state.completed_nodes["one"].status == NodeStatus.COMPLETED
    assert state.completed_nodes["two"].output == {"value": 2}


def test_workflow_artifact_violation(context: RequestContext) -> None:
    sink = InMemoryAuditSink()
    spec = WorkflowSpec(
        name="wf",
        nodes=[
            WorkflowNode(
                name="a",
                handler_name="a",
                artifact_spec=ArtifactSpec(name="a", required_keys=["id"]),
            )
        ],
    )
    orchestrator = WorkflowOrchestrator(
        handlers={"a": lambda ctx, inputs: {"value": 1}},
        audit_sink=sink,
    )
    state = orchestrator.run(context=context, spec=spec)
    assert state.completed_nodes["a"].status == NodeStatus.FAILED
    events = [e.event_type for e in sink.query()]
    assert "artifact_contract_violation" in events


def test_workflow_resume_after_approval(context: RequestContext) -> None:
    sink = InMemoryAuditSink()
    call_log = {"count": 0}

    def node(ctx, inputs):
        call_log["count"] += 1
        if call_log["count"] == 1:
            raise ApprovalRequiredError("needs approval", approval_id="apv-1")
        return {"ok": True}

    spec = WorkflowSpec(
        name="wf",
        nodes=[WorkflowNode(name="hitl", handler_name="hitl")],
    )
    orchestrator = WorkflowOrchestrator(handlers={"hitl": node}, audit_sink=sink)
    state = orchestrator.run(context=context, spec=spec)
    assert state.pending_node == "hitl"
    assert state.pending_approval_id == "apv-1"
    state = orchestrator.resume(context=context, spec=spec, approval_id="apv-1")
    assert state.completed_nodes["hitl"].status == NodeStatus.COMPLETED


def test_workflow_fan_in_merge_node(context: RequestContext) -> None:
    """Two branches converge into a merge node (parallel fan-in template)."""

    def branch_a(_ctx, inputs):
        del inputs
        return {"branch": "a", "v": 10}

    def branch_b(_ctx, inputs):
        del inputs
        return {"branch": "b", "v": 3}

    def merge_node(_ctx, inputs):
        a_out = inputs.get("a") or {}
        b_out = inputs.get("b") or {}
        return {"sum": int(a_out.get("v", 0)) + int(b_out.get("v", 0))}

    spec = WorkflowSpec(
        name="fan-in-demo",
        nodes=[
            WorkflowNode(name="a", handler_name="branch_a"),
            WorkflowNode(name="b", handler_name="branch_b"),
            WorkflowNode(name="merge", handler_name="merge_node", depends_on=["a", "b"]),
        ],
    )
    orchestrator = WorkflowOrchestrator(
        handlers={"branch_a": branch_a, "branch_b": branch_b, "merge_node": merge_node},
    )
    state = orchestrator.run(context=context, spec=spec)
    assert state.completed_nodes["merge"].status == NodeStatus.COMPLETED
    assert state.completed_nodes["merge"].output == {"sum": 13}


def test_workflow_registers_parent_child_run(context: RequestContext) -> None:
    graph = TaskGraphSupervisor()
    graph.register_run(run_id="parent-run", tenant_id=context.tenant_id)
    spec = WorkflowSpec(
        name="wf",
        parent_run_id="parent-run",
        orphan_policy=OrphanPolicy.ADOPT,
        nodes=[WorkflowNode(name="one", handler_name="one")],
    )
    orchestrator = WorkflowOrchestrator(
        handlers={"one": lambda ctx, inputs: {"ok": True}},
        task_graph=graph,
    )

    orchestrator.run(context=context, spec=spec)

    record = graph.get(context.run_id)
    assert record is not None
    assert record.parent_run_id == "parent-run"
    assert record.orphan_policy == OrphanPolicy.ADOPT


def test_strict_harness_handoff_passes(context: RequestContext) -> None:
    from agentium.coordination.artifact_store import ArtifactStore
    from agentium.models.harness_contract import HarnessContract

    store = ArtifactStore()
    spec = WorkflowSpec(
        name="wf",
        nodes=[WorkflowNode(name="one", handler_name="one")],
        harness_contract=HarnessContract(handoff_artifact_keys=["summary.json"]),
    )
    orch = WorkflowOrchestrator(
        handlers={"one": lambda ctx, inputs: {"summary.json": {"ok": True}}},
        artifact_store=store,
        strict_harness_handoff=True,
    )
    state = orch.run(context=context, spec=spec)
    assert state.completed_nodes["one"].status == NodeStatus.COMPLETED
    assert state.completion_error is None


def test_strict_harness_handoff_sets_completion_error(context: RequestContext) -> None:
    from agentium.coordination.artifact_store import ArtifactStore
    from agentium.models.harness_contract import HarnessContract

    store = ArtifactStore()
    spec = WorkflowSpec(
        name="wf",
        nodes=[WorkflowNode(name="one", handler_name="one")],
        harness_contract=HarnessContract(handoff_artifact_keys=["missing.json"]),
    )
    orch = WorkflowOrchestrator(
        handlers={"one": lambda ctx, inputs: {"other": True}},
        artifact_store=store,
        strict_harness_handoff=True,
    )
    state = orch.run(context=context, spec=spec)
    assert state.completed_nodes["one"].status == NodeStatus.COMPLETED
    assert state.completion_error is not None
    assert "missing.json" in (state.completion_error or "")
