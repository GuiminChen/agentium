"""Workflow orchestrator with artifact contracts and HITL resume support."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

from agentium.coordination.artifact_contract import (
    ArtifactSpec,
    ArtifactValidation,
    validate_artifact,
)
from agentium.coordination.artifact_store import ArtifactStore
from agentium.coordination.emergence_guardrails import (
    EmergenceGuardrails,
    GuardrailState,
)
from agentium.core.scheduler import TimeoutLayers, run_with_timeout
from agentium.coordination.task_graph import OrphanPolicy, TaskGraphSupervisor
from agentium.governance.audit_lineage import AuditSink
from agentium.models.context import AuditRecord, RequestContext
from agentium.models.harness_contract import HarnessContract
from agentium.shared.errors import ApprovalRequiredError, PolicyDeniedError

if TYPE_CHECKING:
    from agentium.core.run_cancellation import RunCancelRegistry

NodeHandler = Callable[[RequestContext, Dict[str, Any]], Dict[str, Any]]


class NodeStatus(str, Enum):
    """Per-node execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    AWAITING_APPROVAL = "awaiting_approval"
    CANCELLED = "cancelled"


class WorkflowNode(BaseModel):
    """One node within a workflow specification."""

    name: str = Field(min_length=1)
    handler_name: str = Field(min_length=1)
    artifact_spec: Optional[ArtifactSpec] = None
    timeout_seconds: float = Field(default=30.0, gt=0)
    depends_on: List[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"


class TimeoutLayersModel(BaseModel):
    """Pydantic-friendly mirror of core.scheduler.TimeoutLayers."""

    tool_seconds: float = 30.0
    llm_seconds: float = 60.0
    turn_seconds: float = 120.0
    node_seconds: float = 300.0

    class Config:
        extra = "forbid"

    def to_layers(self) -> TimeoutLayers:
        return TimeoutLayers(
            tool_seconds=self.tool_seconds,
            llm_seconds=self.llm_seconds,
            turn_seconds=self.turn_seconds,
            node_seconds=self.node_seconds,
        )


class WorkflowSpec(BaseModel):
    """Static workflow declaration evaluated by WorkflowOrchestrator."""

    name: str = Field(min_length=1)
    nodes: List[WorkflowNode]
    timeouts: Optional[TimeoutLayersModel] = None
    parent_run_id: Optional[str] = None
    orphan_policy: OrphanPolicy = OrphanPolicy.FAIL
    harness_contract: Optional[HarnessContract] = None

    class Config:
        extra = "forbid"


@dataclass
class NodeResult:
    """Per-node execution result."""

    name: str
    status: NodeStatus
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    artifact_validation: Optional[ArtifactValidation] = None
    approval_id: Optional[str] = None


@dataclass
class WorkflowState:
    """Mutable workflow execution state used for resume."""

    workflow_name: str
    run_id: str
    tenant_id: str
    completed_nodes: Dict[str, NodeResult] = field(default_factory=dict)
    pending_node: Optional[str] = None
    pending_approval_id: Optional[str] = None
    pending_inputs: Optional[Dict[str, Any]] = None
    completion_error: Optional[str] = None


def _copy_workflow_state(state: WorkflowState) -> WorkflowState:
    """Shallow copy of workflow state for safe handoff outside the lock."""

    return WorkflowState(
        workflow_name=state.workflow_name,
        run_id=state.run_id,
        tenant_id=state.tenant_id,
        completed_nodes=dict(state.completed_nodes),
        pending_node=state.pending_node,
        pending_approval_id=state.pending_approval_id,
        pending_inputs=dict(state.pending_inputs) if state.pending_inputs else None,
        completion_error=state.completion_error,
    )


class WorkflowOrchestrator:
    """Sequential dependency-respecting workflow runner with resume support."""

    def __init__(
        self,
        handlers: Dict[str, NodeHandler],
        audit_sink: Optional[AuditSink] = None,
        artifact_store: Optional[ArtifactStore] = None,
        guardrails: Optional[EmergenceGuardrails] = None,
        node_counter_name: str = "workflow.node_completed",
        task_graph: Optional[TaskGraphSupervisor] = None,
        run_cancel_registry: Optional["RunCancelRegistry"] = None,
        strict_harness_handoff: bool = False,
    ) -> None:
        self._handlers = handlers
        self._audit_sink = audit_sink
        self._artifact_store = artifact_store
        self._guardrails = guardrails
        self._node_counter_name = node_counter_name
        self._task_graph = task_graph
        self._run_cancel_registry = run_cancel_registry
        self._strict_harness_handoff = strict_harness_handoff
        self._states: Dict[str, WorkflowState] = {}
        self._lock = threading.Lock()

    def get_state(self, run_id: str) -> Optional[WorkflowState]:
        """Return last known workflow state for run_id, if any."""

        with self._lock:
            s = self._states.get(run_id)
            if s is None:
                return None
            return _copy_workflow_state(s)

    def run(
        self,
        context: RequestContext,
        spec: WorkflowSpec,
        initial_inputs: Optional[Dict[str, Any]] = None,
    ) -> WorkflowState:
        """Run workflow from scratch. Returns the final state snapshot."""

        state = WorkflowState(
            workflow_name=spec.name,
            run_id=context.run_id,
            tenant_id=context.tenant_id,
        )
        if self._task_graph is not None:
            self._task_graph.register_run(
                run_id=context.run_id,
                tenant_id=context.tenant_id,
                parent_run_id=spec.parent_run_id,
                orphan_policy=spec.orphan_policy,
            )
        with self._lock:
            self._states[context.run_id] = state
        return self._drive(context=context, spec=spec, state=state, inputs=initial_inputs or {})

    def resume(
        self,
        context: RequestContext,
        spec: WorkflowSpec,
        approval_id: str,
    ) -> WorkflowState:
        """Resume a workflow blocked at an approval checkpoint."""

        with self._lock:
            state = self._states.get(context.run_id)
        if state is None or state.pending_node is None:
            raise PolicyDeniedError("No suspended workflow state to resume")
        if state.pending_approval_id != approval_id:
            raise PolicyDeniedError("approval_id does not match suspended workflow")
        return self._drive(
            context=context,
            spec=spec,
            state=state,
            inputs=state.pending_inputs or {},
            resume_node=state.pending_node,
            resume_approval_id=approval_id,
        )

    def step_workflow_node(
        self,
        context: RequestContext,
        spec: WorkflowSpec,
        state: WorkflowState,
        node_name: str,
        inputs: Dict[str, Any],
        *,
        resume_node: Optional[str] = None,
        _resume_approval_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Run a single workflow node if it is due.

        Returns merged ``inputs`` on successful completion, or **unchanged** inputs when
        the node was skipped (already completed). Returns ``None`` if the workflow
        halted (failure, guardrail, artifact violation, or approval required).
        """

        node_index = self._index_nodes(spec.nodes)
        node = node_index[node_name]
        if node_name in state.completed_nodes and node_name != resume_node:
            return inputs

        if self._run_cancel_registry is not None and self._run_cancel_registry.is_cancelled(
            context.run_id
        ):
            state.completed_nodes[node_name] = NodeResult(
                name=node_name,
                status=NodeStatus.CANCELLED,
                error="run_cancelled",
            )
            self._audit_node(
                context,
                spec,
                node_name,
                "node_cancelled",
                {"reason": "run_cancelled"},
            )
            return None

        handler = self._handlers.get(node.handler_name)
        if handler is None:
            state.completed_nodes[node_name] = NodeResult(
                name=node_name,
                status=NodeStatus.FAILED,
                error=f"handler_not_found:{node.handler_name}",
            )
            self._audit_node(context, spec, node_name, "node_handler_missing", {})
            return None
        self._audit_node(context, spec, node_name, "node_started", {})
        try:
            output = run_with_timeout(
                work=lambda token, inputs=inputs: handler(context, inputs),
                layer=f"node.{node_name}",
                timeout_seconds=node.timeout_seconds,
            )
        except ApprovalRequiredError as exc:
            state.pending_node = node_name
            state.pending_approval_id = exc.approval_id
            state.pending_inputs = inputs
            state.completed_nodes[node_name] = NodeResult(
                name=node_name,
                status=NodeStatus.AWAITING_APPROVAL,
                approval_id=exc.approval_id,
            )
            self._audit_node(
                context,
                spec,
                node_name,
                "node_awaiting_approval",
                {"approval_id": exc.approval_id},
            )
            return None
        except Exception as exc:  # noqa: BLE001
            state.completed_nodes[node_name] = NodeResult(
                name=node_name,
                status=NodeStatus.FAILED,
                error=str(exc),
            )
            self._audit_node(
                context, spec, node_name, "node_failed", {"error": str(exc)}
            )
            return None
        artifact_validation: Optional[ArtifactValidation] = None
        if node.artifact_spec is not None:
            artifact_validation = validate_artifact(node.artifact_spec, output)
            if not artifact_validation.valid:
                state.completed_nodes[node_name] = NodeResult(
                    name=node_name,
                    status=NodeStatus.FAILED,
                    error=artifact_validation.reason,
                    artifact_validation=artifact_validation,
                )
                self._audit_node(
                    context,
                    spec,
                    node_name,
                    "artifact_contract_violation",
                    {"reason": artifact_validation.reason},
                )
                return None
        normalized_output = output if isinstance(output, dict) else {"value": output}
        artifact_id: Optional[str] = None
        if self._artifact_store is not None:
            parent_ids = tuple(self._completed_artifact_ids(state, node.depends_on))
            stored = self._artifact_store.put(
                workflow=spec.name,
                node=node_name,
                tenant_id=context.tenant_id,
                run_id=context.run_id,
                content=normalized_output,
                parent_ids=parent_ids,
                metadata={"trace_id": context.trace_id},
            )
            artifact_id = stored.artifact_id
            normalized_output = {**normalized_output, "_artifact_id": artifact_id}
        if self._guardrails is not None:
            decision = self._guardrails.try_increment(
                counter=self._node_counter_name,
                tenant_id=context.tenant_id,
                scope_id=spec.name,
            )
            if decision.state == GuardrailState.TRIPPED:
                state.completed_nodes[node_name] = NodeResult(
                    name=node_name,
                    status=NodeStatus.FAILED,
                    error=f"emergence_guardrail_tripped:{decision.counter}",
                )
                self._audit_node(
                    context,
                    spec,
                    node_name,
                    "emergence_guardrail_tripped",
                    {
                        "counter": decision.counter,
                        "current": decision.current,
                        "hard_limit": decision.hard_limit,
                    },
                )
                return None
        state.completed_nodes[node_name] = NodeResult(
            name=node_name,
            status=NodeStatus.COMPLETED,
            output=normalized_output,
            artifact_validation=artifact_validation,
        )
        merged_inputs = {
            **inputs,
            node_name: state.completed_nodes[node_name].output,
        }
        self._audit_node(
            context,
            spec,
            node_name,
            "node_completed",
            {
                "checksum": artifact_validation.checksum_sha256
                if artifact_validation
                else None,
                "artifact_id": artifact_id,
            },
        )
        state.pending_node = None
        state.pending_approval_id = None
        state.pending_inputs = None
        return merged_inputs

    def _drive(
        self,
        context: RequestContext,
        spec: WorkflowSpec,
        state: WorkflowState,
        inputs: Dict[str, Any],
        resume_node: Optional[str] = None,
        resume_approval_id: Optional[str] = None,
    ) -> WorkflowState:
        order = _topological_order(spec.nodes)
        active_resume = resume_node
        for node_name in order:
            if self._run_cancel_registry is not None and self._run_cancel_registry.is_cancelled(
                context.run_id
            ):
                return state
            if node_name in state.completed_nodes and node_name != active_resume:
                continue
            next_inputs = self.step_workflow_node(
                context,
                spec,
                state,
                node_name,
                inputs,
                resume_node=active_resume,
                _resume_approval_id=resume_approval_id,
            )
            if next_inputs is None:
                return state
            inputs = next_inputs
            active_resume = None
        all_completed = bool(order) and all(
            state.completed_nodes.get(n) is not None
            and state.completed_nodes[n].status == NodeStatus.COMPLETED
            for n in order
        )
        if (
            all_completed
            and self._strict_harness_handoff
            and self._artifact_store is not None
            and spec.harness_contract is not None
            and spec.harness_contract.handoff_artifact_keys
        ):
            from agentium.coordination.harness_handoff import verify_handoff_artifact_keys

            vr = verify_handoff_artifact_keys(
                self._artifact_store,
                tenant_id=context.tenant_id,
                run_id=context.run_id,
                keys=spec.harness_contract.handoff_artifact_keys,
            )
            if not vr.ok:
                state.completion_error = f"harness_handoff:{','.join(vr.missing_keys)}"
                self._audit_node(
                    context,
                    spec,
                    order[-1],
                    "harness_handoff_violation",
                    {"missing_keys": list(vr.missing_keys)},
                )
        return state

    def _completed_artifact_ids(
        self, state: WorkflowState, dep_names: List[str]
    ) -> List[str]:
        ids: List[str] = []
        for dep in dep_names:
            result = state.completed_nodes.get(dep)
            if result is None or result.output is None:
                continue
            artifact_id = result.output.get("_artifact_id")
            if isinstance(artifact_id, str):
                ids.append(artifact_id)
        return ids

    def _index_nodes(self, nodes: List[WorkflowNode]) -> Dict[str, WorkflowNode]:
        return {node.name: node for node in nodes}

    def _audit_node(
        self,
        context: RequestContext,
        spec: WorkflowSpec,
        node_name: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        if self._audit_sink is None:
            return
        try:
            self._audit_sink.append(
                AuditRecord(
                    event_type=event_type,
                    tenant_id=context.tenant_id,
                    run_id=context.run_id,
                    payload={
                        "workflow": spec.name,
                        "node": node_name,
                        **payload,
                    },
                )
            )
        except Exception:
            pass


def _topological_order(nodes: List[WorkflowNode]) -> List[str]:
    """Return node names in dependency-respecting order. Raises on cycles."""

    name_to_node = {node.name: node for node in nodes}
    visited: Dict[str, str] = {}
    order: List[str] = []

    def visit(name: str) -> None:
        state = visited.get(name)
        if state == "perm":
            return
        if state == "temp":
            raise ValueError(f"workflow has dependency cycle through {name}")
        visited[name] = "temp"
        node = name_to_node.get(name)
        if node is None:
            raise ValueError(f"unknown node referenced as dependency: {name}")
        for dep in node.depends_on:
            visit(dep)
        visited[name] = "perm"
        order.append(name)

    for node in nodes:
        visit(node.name)
    return order
