"""Orchestration engine protocol."""

from __future__ import annotations

from typing import Any, Dict, Optional, Protocol

from agentium.coordination.workflow_orchestrator import WorkflowSpec, WorkflowState
from agentium.models.context import RequestContext


class OrchestrationEngine(Protocol):
    """Backend that runs :class:`WorkflowSpec` instances with resume support."""

    def run(
        self,
        context: RequestContext,
        spec: WorkflowSpec,
        initial_inputs: Optional[Dict[str, Any]] = None,
    ) -> WorkflowState: ...

    def get_state(self, run_id: str) -> Optional[WorkflowState]: ...

    def resume(
        self,
        context: RequestContext,
        spec: WorkflowSpec,
        approval_id: str,
    ) -> WorkflowState: ...


__all__ = ["OrchestrationEngine"]
