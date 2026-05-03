"""Pluggable self-learning / evolution loops (governed via :class:`ProposalQueue`)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Protocol

from pydantic import BaseModel, Field

from agentium.governance.audit_lineage import AuditSink
from agentium.governance.proposal_queue import ProposalKind, ProposalQueue
from agentium.models.context import AuditRecord, RequestContext

if TYPE_CHECKING:
    from agentium.app.plugins_config import EvolutionPluginConfigSection


class TrajectoryEvent(BaseModel):
    """One step or observation in an execution trajectory (sanitized)."""

    step_type: str = Field(min_length=1)
    payload: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class TrajectoryBatch(BaseModel):
    """Batch input for evolution plugins."""

    run_id: str = Field(min_length=1)
    events: List[TrajectoryEvent] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class EvolutionPlugin(Protocol):
    """Consumes trajectories and may submit governed proposals (never mutates policy)."""

    def on_trajectory(self, context: RequestContext, batch: TrajectoryBatch) -> None: ...


class NativeEvolutionPlugin:
    """No-op evolution (default)."""

    def on_trajectory(self, context: RequestContext, batch: TrajectoryBatch) -> None:
        return None


class HermesClassClosedLoopEvolutionPlugin:
    """Clean-room closed loop inspired by public Hermes / Evolver-style narratives.

    Produces proposal queue entries only; does not alter manifests or tools.
    Prior art (public descriptions): Nous Hermes Agent architecture docs;
    EvoMap Evolver self-evolution engine discussions — independent implementation.
    """

    def __init__(
        self,
        proposal_queue: ProposalQueue,
        section: EvolutionPluginConfigSection,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._queue = proposal_queue
        self._section = section
        self._audit = audit_sink

    def on_trajectory(self, context: RequestContext, batch: TrajectoryBatch) -> None:
        if not batch.events:
            return
        max_n = self._section.hermes_class.max_proposals_per_invocation
        kinds = (
            ProposalKind.PROMPT_TEMPLATE,
            ProposalKind.MEMORY_PROMOTION,
            ProposalKind.PREFERENCE_UPDATE,
        )
        for i, event in enumerate(batch.events[:max_n]):
            kind = kinds[i % len(kinds)]
            self._queue.submit(
                context,
                kind,
                {
                    "source": "hermes_class_loop",
                    "run_id": batch.run_id,
                    "step_index": i,
                    "step_type": event.step_type,
                    "suggestion": event.payload,
                },
            )
        if self._audit is not None:
            try:
                self._audit.append(
                    AuditRecord(
                        event_type="evolution_hermes_class_submitted",
                        tenant_id=context.tenant_id,
                        run_id=context.run_id,
                        payload={
                            "batch_run_id": batch.run_id,
                            "proposal_count": min(len(batch.events), max_n),
                        },
                    )
                )
            except Exception:
                pass


def build_evolution_plugin(
    cfg: EvolutionPluginConfigSection,
    proposal_queue: ProposalQueue,
    audit_sink: AuditSink | None,
) -> EvolutionPlugin:
    if cfg.plugin == "native":
        return NativeEvolutionPlugin()
    if cfg.plugin == "hermes_class":
        return HermesClassClosedLoopEvolutionPlugin(
            proposal_queue, cfg, audit_sink=audit_sink
        )
    raise ValueError(f"unknown evolution.plugin: {cfg.plugin!r}")


__all__ = [
    "EvolutionPlugin",
    "HermesClassClosedLoopEvolutionPlugin",
    "NativeEvolutionPlugin",
    "TrajectoryBatch",
    "TrajectoryEvent",
    "build_evolution_plugin",
]
