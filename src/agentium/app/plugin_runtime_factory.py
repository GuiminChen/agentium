"""Assemble orchestration, memory, and evolution plugins from YAML config."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict

from agentium.app.plugins_config import PluginsConfig, plugins_fingerprint_payload
from agentium.app.settings import AppSettings
from agentium.coordination.orchestration.engine import OrchestrationEngine
from agentium.coordination.workflow_orchestrator import NodeHandler, WorkflowOrchestrator
from agentium.governance.audit_lineage import AuditSink
from agentium.governance.evolution_plugin import EvolutionPlugin, build_evolution_plugin
from agentium.governance.proposal_queue import ProposalQueue
from agentium.memory.factory import build_memory_backend
from agentium.memory.memory_service import MemoryBackend

if TYPE_CHECKING:
    from agentium.coordination.artifact_store import ArtifactStore
    from agentium.coordination.emergence_guardrails import EmergenceGuardrails
    from agentium.coordination.task_graph import TaskGraphSupervisor


@dataclass(frozen=True)
class PluginRuntime:
    """Resolved plugin instances for one process."""

    orchestration_engine: OrchestrationEngine
    memory_backend: MemoryBackend
    evolution_plugin: EvolutionPlugin
    proposal_queue: ProposalQueue
    fingerprint: dict


def build_workflow_orchestrator(
    *,
    plugins: PluginsConfig,
    handlers: Dict[str, NodeHandler],
    audit_sink: AuditSink | None,
    artifact_store: "ArtifactStore | None",
    guardrails: "EmergenceGuardrails | None",
    task_graph: "TaskGraphSupervisor | None",
    run_cancel_registry: "object | None" = None,
) -> OrchestrationEngine:
    """Return native or LangGraph orchestration engine sharing handler registry."""

    native = WorkflowOrchestrator(
        handlers=handlers,
        audit_sink=audit_sink,
        artifact_store=artifact_store,
        guardrails=guardrails,
        task_graph=task_graph,
        run_cancel_registry=run_cancel_registry,
    )
    if plugins.orchestration.backend == "native":
        return native
    if plugins.orchestration.backend == "langgraph":
        from agentium.coordination.orchestration.langgraph_engine import (
            LangGraphOrchestrationEngine,
        )

        return LangGraphOrchestrationEngine(native)
    raise ValueError(
        f"unknown orchestration.backend: {plugins.orchestration.backend!r}"
    )


def build_plugin_runtime(
    settings: AppSettings,
    *,
    handlers: Dict[str, NodeHandler],
    audit_sink: AuditSink | None,
    artifact_store: "ArtifactStore | None",
    guardrails: "EmergenceGuardrails | None",
    task_graph: "TaskGraphSupervisor | None",
    run_cancel_registry: object | None = None,
) -> PluginRuntime:
    """Build memory backend, proposal queue, evolution plugin, and orchestration engine."""

    plugins = settings.plugins
    memory_backend = build_memory_backend(plugins.memory, settings.data_dir)
    proposal_queue = ProposalQueue(audit_sink=audit_sink)
    evolution = build_evolution_plugin(
        plugins.evolution,
        proposal_queue,
        audit_sink=audit_sink,
    )
    orch = build_workflow_orchestrator(
        plugins=plugins,
        handlers=handlers,
        audit_sink=audit_sink,
        artifact_store=artifact_store,
        guardrails=guardrails,
        task_graph=task_graph,
        run_cancel_registry=run_cancel_registry,
    )
    fp = plugins_fingerprint_payload(plugins)
    return PluginRuntime(
        orchestration_engine=orch,
        memory_backend=memory_backend,
        evolution_plugin=evolution,
        proposal_queue=proposal_queue,
        fingerprint=fp,
    )


__all__ = ["PluginRuntime", "build_plugin_runtime", "build_workflow_orchestrator"]
