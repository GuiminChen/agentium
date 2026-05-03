"""DeepResearch pipeline: governed multi-stage research workflow.

Implements PRD §3.7 / §3.18 and the technical design's ``research`` profile:

1. **Plan** – produce a structured research plan from the user query.
2. **Search** – fan out search calls (or a deterministic stub in tests).
3. **Synthesize** – fold the search results into citation-bearing notes.
4. **Critique** – self-critique pass that flags missing evidence.
5. **Report** – emit the final report with full citation lineage.

The pipeline is built on :class:`WorkflowOrchestrator` so the existing
governance, artifact, and emergence guardrails are reused.  A handler
registry (``ResearchHandlers``) makes it trivial to swap stub handlers
for production LLM-backed implementations during deployment.

Each stage's output is persisted via :class:`ArtifactStore`, giving the
auditor a deterministic citation chain from query to final report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional

from agentium.coordination.artifact_contract import ArtifactSpec
from agentium.coordination.artifact_store import ArtifactStore
from agentium.coordination.emergence_guardrails import EmergenceGuardrails
from agentium.coordination.orchestration.engine import OrchestrationEngine
from agentium.coordination.workflow_orchestrator import (
    NodeStatus,
    WorkflowNode,
    WorkflowOrchestrator,
    WorkflowSpec,
    WorkflowState,
)
from agentium.coordination.task_graph import TaskGraphSupervisor
from agentium.governance.audit_lineage import AuditSink
from agentium.governance.evolution_plugin import EvolutionPlugin, TrajectoryBatch, TrajectoryEvent
from agentium.models.context import RequestContext


if TYPE_CHECKING:
    from agentium.core.run_cancellation import RunCancelRegistry


HandlerFn = Callable[[RequestContext, Dict[str, Any]], Dict[str, Any]]

VERTICAL_SCENARIO_PACK_SEMVER = "0.1.0"
"""Semver for bundled vertical / scenario metadata in stub reports (M5 regression anchor)."""


@dataclass
class ResearchHandlers:
    """Pluggable handler set for the DeepResearch pipeline.

    All handlers receive ``(context, inputs)`` and must return a
    :class:`dict` matching the artifact contract for the corresponding
    stage.  Default no-op handlers are provided so the pipeline runs in
    test environments without any external dependencies.
    """

    plan: HandlerFn
    search: HandlerFn
    synthesize: HandlerFn
    critique: HandlerFn
    report: HandlerFn

    def to_handler_map(self) -> Dict[str, HandlerFn]:
        return {
            "research.plan": self.plan,
            "research.search": self.search,
            "research.synthesize": self.synthesize,
            "research.critique": self.critique,
            "research.report": self.report,
        }


@dataclass
class ResearchOutcome:
    """Final result of :meth:`DeepResearchPipeline.run`."""

    state: WorkflowState
    artifacts: List[str] = field(default_factory=list)
    success: bool = False
    report: Optional[Mapping[str, Any]] = None


def build_trajectory_from_research_outcome(
    context: RequestContext,
    spec: WorkflowSpec,
    outcome: ResearchOutcome,
) -> TrajectoryBatch:
    """Summarize a completed DeepResearch run (status + output keys only, no full bodies)."""

    events: List[TrajectoryEvent] = []
    state = outcome.state
    for node in spec.nodes:
        result = state.completed_nodes.get(node.name)
        payload: Dict[str, Any] = {"handler": node.handler_name, "node": node.name}
        if result is not None:
            payload["status"] = (
                result.status.value if hasattr(result.status, "value") else str(result.status)
            )
            if result.output and isinstance(result.output, dict):
                payload["output_keys"] = list(result.output.keys())[:24]
            if result.error:
                payload["error"] = str(result.error)[:512]
        else:
            payload["status"] = "missing"
        events.append(
            TrajectoryEvent(
                step_type=f"deep_research.node.{node.name}",
                payload=payload,
            )
        )
    events.append(
        TrajectoryEvent(
            step_type="deep_research.run.summary",
            payload={
                "pipeline_success": outcome.success,
                "artifact_count": len(outcome.artifacts),
                "query_present": True,
            },
        )
    )
    return TrajectoryBatch(run_id=context.run_id, events=events)


def default_artifact_specs() -> Dict[str, ArtifactSpec]:
    """Return contract specs enforcing the research stage outputs."""

    return {
        "plan": ArtifactSpec(
            name="research.plan",
            required_keys=["topic", "questions"],
        ),
        "search": ArtifactSpec(
            name="research.search",
            required_keys=["results"],
        ),
        "synthesize": ArtifactSpec(
            name="research.synthesize",
            required_keys=["notes", "citations"],
        ),
        "critique": ArtifactSpec(
            name="research.critique",
            required_keys=["issues"],
        ),
        "report": ArtifactSpec(
            name="research.report",
            required_keys=["title", "summary", "citations"],
        ),
    }


def default_workflow_spec(timeout_seconds: float = 30.0) -> WorkflowSpec:
    """Build the canonical research workflow spec."""

    specs = default_artifact_specs()
    return WorkflowSpec(
        name="deepresearch.v1",
        nodes=[
            WorkflowNode(
                name="plan",
                handler_name="research.plan",
                artifact_spec=specs["plan"],
                timeout_seconds=timeout_seconds,
            ),
            WorkflowNode(
                name="search",
                handler_name="research.search",
                artifact_spec=specs["search"],
                timeout_seconds=timeout_seconds,
                depends_on=["plan"],
            ),
            WorkflowNode(
                name="synthesize",
                handler_name="research.synthesize",
                artifact_spec=specs["synthesize"],
                timeout_seconds=timeout_seconds,
                depends_on=["search"],
            ),
            WorkflowNode(
                name="critique",
                handler_name="research.critique",
                artifact_spec=specs["critique"],
                timeout_seconds=timeout_seconds,
                depends_on=["synthesize"],
            ),
            WorkflowNode(
                name="report",
                handler_name="research.report",
                artifact_spec=specs["report"],
                timeout_seconds=timeout_seconds,
                depends_on=["critique"],
            ),
        ],
    )


class DeepResearchPipeline:
    """High-level wrapper that drives :class:`WorkflowOrchestrator`."""

    def __init__(
        self,
        handlers: ResearchHandlers,
        *,
        artifact_store: Optional[ArtifactStore] = None,
        guardrails: Optional[EmergenceGuardrails] = None,
        audit_sink: Optional[AuditSink] = None,
        spec: Optional[WorkflowSpec] = None,
        task_graph: Optional[TaskGraphSupervisor] = None,
        orchestration_engine: Optional[OrchestrationEngine] = None,
        evolution_plugin: Optional[EvolutionPlugin] = None,
        run_cancel_registry: Optional["RunCancelRegistry"] = None,
    ) -> None:
        self._spec = spec or default_workflow_spec()
        self._artifact_store = artifact_store
        self._evolution_plugin = evolution_plugin
        if orchestration_engine is not None:
            self._engine: OrchestrationEngine = orchestration_engine
        else:
            self._engine = WorkflowOrchestrator(
                handlers=handlers.to_handler_map(),
                audit_sink=audit_sink,
                artifact_store=artifact_store,
                guardrails=guardrails,
                task_graph=task_graph,
                run_cancel_registry=run_cancel_registry,
            )

    @property
    def orchestrator(self) -> OrchestrationEngine:
        """Workflow engine (native, LangGraph-wrapped, etc.) for run/resume APIs."""

        return self._engine

    @property
    def spec(self) -> WorkflowSpec:
        return self._spec

    def run(
        self,
        context: RequestContext,
        query: str,
        *,
        extras: Optional[Mapping[str, Any]] = None,
    ) -> ResearchOutcome:
        if not query:
            raise ValueError("query must not be empty")
        inputs = {"query": query, "extras": dict(extras or {})}
        state = self._engine.run(context=context, spec=self._spec, initial_inputs=inputs)
        artifact_ids: List[str] = []
        report: Optional[Mapping[str, Any]] = None
        success = True
        for node in self._spec.nodes:
            result = state.completed_nodes.get(node.name)
            if result is None or result.status != NodeStatus.COMPLETED:
                success = False
                continue
            if node.name == "report":
                report = result.output
        if self._artifact_store is not None:
            for artifact in self._artifact_store.list_for_run(run_id=context.run_id):
                artifact_ids.append(artifact.artifact_id)
        outcome = ResearchOutcome(
            state=state,
            artifacts=artifact_ids,
            success=success and report is not None,
            report=report,
        )
        if self._evolution_plugin is not None:
            from agentium.governance.evolution_trajectory import sanitize_trajectory_batch

            batch = build_trajectory_from_research_outcome(context, self._spec, outcome)
            self._evolution_plugin.on_trajectory(context, sanitize_trajectory_batch(batch))
        return outcome

    def workflow_snapshot_for_http(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Serializable workflow progress for GET /v1/research/{run_id} / workflow APIs."""

        state = self._engine.get_state(run_id)
        if state is None:
            return None
        spec_nodes = [{"name": n.name, "depends_on": list(n.depends_on)} for n in self._spec.nodes]
        completed = []
        for name, res in sorted(state.completed_nodes.items(), key=lambda x: x[0]):
            completed.append(
                {
                    "node": name,
                    "status": res.status.value,
                    "error": res.error,
                    "approval_id": res.approval_id,
                    "has_output": res.output is not None,
                }
            )
        return {
            "workflow_name": state.workflow_name,
            "run_id": state.run_id,
            "tenant_id": state.tenant_id,
            "pending_node": state.pending_node,
            "pending_approval_id": state.pending_approval_id,
            "spec_nodes": spec_nodes,
            "completed_nodes": completed,
        }


def stub_handlers() -> ResearchHandlers:
    """Deterministic handlers used by tests and offline environments."""

    def plan(_: RequestContext, inputs: Dict[str, Any]) -> Dict[str, Any]:
        query = inputs.get("query", "")
        return {
            "topic": query,
            "questions": [f"What is known about: {query}?"],
        }

    def search(_: RequestContext, inputs: Dict[str, Any]) -> Dict[str, Any]:
        query = inputs.get("query", "")
        return {
            "results": [
                {"url": f"about:{query}", "snippet": f"stub result for {query}"}
            ],
        }

    def synthesize(_: RequestContext, inputs: Dict[str, Any]) -> Dict[str, Any]:
        query = inputs.get("query", "")
        return {
            "notes": [f"Synthesised note about {query}"],
            "citations": [{"id": "c1", "url": f"about:{query}"}],
        }

    def critique(_: RequestContext, inputs: Dict[str, Any]) -> Dict[str, Any]:
        return {"issues": []}

    def report(_: RequestContext, inputs: Dict[str, Any]) -> Dict[str, Any]:
        query = inputs.get("query", "")
        extras = inputs.get("extras") if isinstance(inputs.get("extras"), dict) else {}
        vert = str(extras.get("vertical_template") or "general")
        title_suffix = "" if vert == "general" else f" [{vert}]"
        return {
            "title": f"Research report: {query}{title_suffix}",
            "summary": f"Summary of stub research for {query}",
            "citations": [{"id": "c1", "url": f"about:{query}"}],
            "vertical_template": vert,
            "vertical_pack_semver": VERTICAL_SCENARIO_PACK_SEMVER,
        }

    return ResearchHandlers(
        plan=plan,
        search=search,
        synthesize=synthesize,
        critique=critique,
        report=report,
    )


__all__ = [
    "DeepResearchPipeline",
    "HandlerFn",
    "ResearchHandlers",
    "ResearchOutcome",
    "VERTICAL_SCENARIO_PACK_SEMVER",
    "build_trajectory_from_research_outcome",
    "default_artifact_specs",
    "default_workflow_spec",
    "stub_handlers",
]
