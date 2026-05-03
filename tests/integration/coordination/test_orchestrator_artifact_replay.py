"""Crash-recovery integration test for ArtifactStore + WorkflowOrchestrator."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.coordination.artifact_store import ArtifactStore
from agentium.coordination.emergence_guardrails import (
    EmergenceGuardrails,
    GuardrailLimit,
)
from agentium.coordination.workflow_orchestrator import (
    NodeStatus,
    WorkflowNode,
    WorkflowOrchestrator,
    WorkflowSpec,
)
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.models.context import RequestContext


def _ctx(run_id: str) -> RequestContext:
    return RequestContext(
        request_id="r-" + run_id,
        run_id=run_id,
        tenant_id="tenant-replay",
        user_id="user",
        trace_id="trace-" + run_id,
    )


def test_orchestrator_persists_artifacts_and_replays(tmp_path: Path):
    persist = tmp_path / "art.jsonl"
    store = ArtifactStore(persist_path=persist)
    audit = InMemoryAuditSink()

    handlers = {
        "first": lambda ctx, inputs: {"value": 1},
        "second": lambda ctx, inputs: {"value": inputs["first"]["value"] + 2},
    }
    spec = WorkflowSpec(
        name="recovery-wf",
        nodes=[
            WorkflowNode(name="first", handler_name="first"),
            WorkflowNode(name="second", handler_name="second", depends_on=["first"]),
        ],
    )
    orch = WorkflowOrchestrator(
        handlers=handlers, audit_sink=audit, artifact_store=store
    )
    state = orch.run(_ctx("run-1"), spec)
    assert state.completed_nodes["second"].status == NodeStatus.COMPLETED
    assert state.completed_nodes["second"].output["value"] == 3

    fresh_store = ArtifactStore(persist_path=persist)
    artifacts = sorted(fresh_store.replay(), key=lambda a: a.node)
    nodes = [a.node for a in artifacts]
    assert nodes == ["first", "second"]
    second = [a for a in artifacts if a.node == "second"][0]
    first = [a for a in artifacts if a.node == "first"][0]
    assert second.parent_ids == (first.artifact_id,)


def test_orchestrator_trips_emergence_guardrail(tmp_path: Path):
    audit = InMemoryAuditSink()
    guardrails = EmergenceGuardrails(
        limits={"workflow.node_completed": GuardrailLimit(warn_threshold=0, hard_limit=1)}
    )
    handlers = {
        "first": lambda ctx, inputs: {"v": 1},
        "second": lambda ctx, inputs: {"v": 2},
    }
    spec = WorkflowSpec(
        name="trip-wf",
        nodes=[
            WorkflowNode(name="first", handler_name="first"),
            WorkflowNode(name="second", handler_name="second", depends_on=["first"]),
        ],
    )
    orch = WorkflowOrchestrator(
        handlers=handlers, audit_sink=audit, guardrails=guardrails
    )
    state = orch.run(_ctx("run-trip"), spec)
    assert state.completed_nodes["first"].status == NodeStatus.COMPLETED
    assert state.completed_nodes["second"].status == NodeStatus.FAILED
    assert "emergence_guardrail_tripped" in (state.completed_nodes["second"].error or "")
    events = [r.event_type for r in audit.query(run_id="run-trip")]
    assert "emergence_guardrail_tripped" in events
