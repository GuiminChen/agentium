"""LangGraph-backed orchestration: one graph node per workflow node (DAG edges)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, cast

from agentium.coordination.workflow_orchestrator import (
    WorkflowOrchestrator,
    WorkflowSpec,
    WorkflowState,
    _topological_order,
)
from agentium.models.context import RequestContext


def _leaf_nodes(spec: WorkflowSpec) -> List[str]:
    dependents: Set[str] = set()
    for n in spec.nodes:
        dependents.update(n.depends_on)
    return [n.name for n in spec.nodes if n.name not in dependents]


class LangGraphOrchestrationEngine:
    """Runs workflows via LangGraph while sharing state with a native orchestrator."""

    def __init__(self, orchestrator: WorkflowOrchestrator) -> None:
        self._orch = orchestrator

    def get_state(self, run_id: str) -> Optional[WorkflowState]:
        return self._orch.get_state(run_id)

    def resume(
        self,
        context: RequestContext,
        spec: WorkflowSpec,
        approval_id: str,
    ) -> WorkflowState:
        """Always resume through the native orchestrator (HITL-safe)."""

        return self._orch.resume(context, spec, approval_id)

    def run(
        self,
        context: RequestContext,
        spec: WorkflowSpec,
        initial_inputs: Optional[Dict[str, Any]] = None,
    ) -> WorkflowState:
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as exc:
            raise RuntimeError(
                "LangGraph backend selected but langgraph is not installed. "
                "Install with: pip install 'agentium[langgraph]'"
            ) from exc

        initial_inputs = initial_inputs or {}
        state = WorkflowState(
            workflow_name=spec.name,
            run_id=context.run_id,
            tenant_id=context.tenant_id,
        )
        if self._orch._task_graph is not None:
            self._orch._task_graph.register_run(
                run_id=context.run_id,
                tenant_id=context.tenant_id,
                parent_run_id=spec.parent_run_id,
                orphan_policy=spec.orphan_policy,
            )
        with self._orch._lock:
            self._orch._states[context.run_id] = state

        order = _topological_order(spec.nodes)
        graph = StateGraph(dict)

        def make_runner(node_name: str):
            def _run(st: Dict[str, Any]) -> Dict[str, Any]:
                if st.get("halted"):
                    return {}
                ctx = cast(RequestContext, st["context"])
                reg = getattr(self._orch, "_run_cancel_registry", None)
                if reg is not None and reg.is_cancelled(ctx.run_id):
                    return {"halted": True, "inputs": st["inputs"], "resume_node": None}
                sp = cast(WorkflowSpec, st["spec"])
                wf = cast(WorkflowState, st["wf_state"])
                inp = cast(Dict[str, Any], st["inputs"])
                rn = cast(Optional[str], st.get("resume_node"))
                out = self._orch.step_workflow_node(
                    ctx, sp, wf, node_name, inp, resume_node=rn
                )
                if out is None:
                    return {"halted": True, "inputs": inp, "resume_node": None}
                return {
                    "halted": False,
                    "inputs": out,
                    "resume_node": None,
                }

            return _run

        for name in order:
            graph.add_node(name, make_runner(name))

        roots = [n.name for n in spec.nodes if not n.depends_on]
        if not roots:
            raise ValueError("workflow spec must contain at least one root node")
        if len(roots) == 1:
            graph.add_edge(START, roots[0])
        else:
            def _fan_in(_: Dict[str, Any]) -> Dict[str, Any]:
                return {}

            graph.add_node("__fan_in__", _fan_in)
            graph.add_edge(START, "__fan_in__")
            for r in roots:
                graph.add_edge("__fan_in__", r)

        node_by_name = {n.name: n for n in spec.nodes}
        for name in order:
            node = node_by_name[name]
            for dep in node.depends_on:
                graph.add_edge(dep, name)

        for leaf in _leaf_nodes(spec):
            graph.add_edge(leaf, END)

        app = graph.compile()
        app.invoke(
            {
                "context": context,
                "spec": spec,
                "wf_state": state,
                "inputs": dict(initial_inputs),
                "resume_node": None,
                "halted": False,
            }
        )
        return state


__all__ = ["LangGraphOrchestrationEngine"]