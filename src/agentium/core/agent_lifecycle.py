"""OS-style lifecycle tracking for agent runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Dict, Optional

from agentium.models.context import RequestContext


class AgentState(str, Enum):
    """Lifecycle states for one agent run."""

    CREATED = "created"
    READY = "ready"
    RUNNING = "running"
    BLOCKED_HITL = "blocked_hitl"
    BLOCKED_IO = "blocked_io"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CLEANING = "cleaning"
    CLEANED = "cleaned"
    FAILED = "failed"
    KILLED = "killed"


class AgentLifecycleError(RuntimeError):
    """Raised when a lifecycle transition is invalid."""


@dataclass(frozen=True)
class AgentLifecycleRecord:
    """Snapshot of one run lifecycle state."""

    run_id: str
    tenant_id: str
    state: AgentState
    reason: Optional[str] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AgentLifecycleManager:
    """Thread-safe finite-state machine for agent run lifecycles."""

    _ALLOWED: Dict[AgentState, tuple[AgentState, ...]] = {
        AgentState.CREATED: (AgentState.READY, AgentState.KILLED, AgentState.FAILED),
        AgentState.READY: (AgentState.RUNNING, AgentState.STOPPED, AgentState.KILLED),
        AgentState.RUNNING: (
            AgentState.BLOCKED_HITL,
            AgentState.BLOCKED_IO,
            AgentState.STOPPING,
            AgentState.STOPPED,
            AgentState.FAILED,
            AgentState.KILLED,
        ),
        AgentState.BLOCKED_HITL: (
            AgentState.READY,
            AgentState.STOPPED,
            AgentState.FAILED,
            AgentState.KILLED,
        ),
        AgentState.BLOCKED_IO: (
            AgentState.READY,
            AgentState.STOPPED,
            AgentState.FAILED,
            AgentState.KILLED,
        ),
        AgentState.STOPPING: (AgentState.STOPPED, AgentState.KILLED),
        AgentState.STOPPED: (AgentState.CLEANING, AgentState.CLEANED),
        AgentState.CLEANING: (AgentState.CLEANED,),
        AgentState.FAILED: (AgentState.CLEANING, AgentState.CLEANED),
        AgentState.KILLED: (AgentState.CLEANING, AgentState.CLEANED),
        AgentState.CLEANED: (),
    }

    def __init__(self) -> None:
        self._records: Dict[str, AgentLifecycleRecord] = {}
        self._lock = Lock()

    def create(self, context: RequestContext) -> AgentLifecycleRecord:
        """Create a lifecycle record for a run."""

        with self._lock:
            record = AgentLifecycleRecord(
                run_id=context.run_id,
                tenant_id=context.tenant_id,
                state=AgentState.CREATED,
            )
            self._records[context.run_id] = record
            return record

    def ensure_created(self, context: RequestContext) -> AgentLifecycleRecord:
        """Return existing record or create it for a run."""

        with self._lock:
            record = self._records.get(context.run_id)
        if record is not None:
            return record
        return self.create(context)

    def ready(self, run_id: str, reason: Optional[str] = None) -> AgentLifecycleRecord:
        """Mark a created or blocked run as ready."""

        return self._transition(run_id, AgentState.READY, reason)

    def start(self, run_id: str, reason: Optional[str] = None) -> AgentLifecycleRecord:
        """Mark a ready run as running."""

        return self._transition(run_id, AgentState.RUNNING, reason)

    def block_hitl(self, run_id: str, reason: str) -> AgentLifecycleRecord:
        """Mark a running run as blocked on human approval."""

        return self._transition(run_id, AgentState.BLOCKED_HITL, reason)

    def block_io(self, run_id: str, reason: str) -> AgentLifecycleRecord:
        """Mark a running run as blocked on I/O."""

        return self._transition(run_id, AgentState.BLOCKED_IO, reason)

    def resume(self, run_id: str, reason: Optional[str] = None) -> AgentLifecycleRecord:
        """Move a blocked run back to ready."""

        return self._transition(run_id, AgentState.READY, reason)

    def stop(self, run_id: str, reason: Optional[str] = None) -> AgentLifecycleRecord:
        """Gracefully stop a running or ready run."""

        current = self.get(run_id)
        if current.state == AgentState.RUNNING:
            self._transition(run_id, AgentState.STOPPING, reason)
        return self._transition(run_id, AgentState.STOPPED, reason)

    def cleanup(self, run_id: str, reason: Optional[str] = None) -> AgentLifecycleRecord:
        """Clean resources for a stopped, failed, or killed run."""

        current = self.get(run_id)
        if current.state in {AgentState.STOPPED, AgentState.FAILED, AgentState.KILLED}:
            self._transition(run_id, AgentState.CLEANING, reason)
        return self._transition(run_id, AgentState.CLEANED, reason)

    def fail(self, run_id: str, reason: str) -> AgentLifecycleRecord:
        """Mark a run as failed."""

        return self._transition(run_id, AgentState.FAILED, reason)

    def kill(self, run_id: str, reason: str) -> AgentLifecycleRecord:
        """Force-kill a run after cooperative stop is insufficient."""

        return self._transition(run_id, AgentState.KILLED, reason)

    def get(self, run_id: str) -> AgentLifecycleRecord:
        """Return current lifecycle snapshot for one run."""

        with self._lock:
            record = self._records.get(run_id)
        if record is None:
            raise AgentLifecycleError(f"run not found: {run_id}")
        return record

    def _transition(
        self, run_id: str, next_state: AgentState, reason: Optional[str]
    ) -> AgentLifecycleRecord:
        with self._lock:
            current = self._records.get(run_id)
            if current is None:
                raise AgentLifecycleError(f"run not found: {run_id}")
            allowed = self._ALLOWED[current.state]
            if next_state not in allowed:
                raise AgentLifecycleError(
                    f"invalid lifecycle transition: {current.state.value}->{next_state.value}"
                )
            record = AgentLifecycleRecord(
                run_id=current.run_id,
                tenant_id=current.tenant_id,
                state=next_state,
                reason=reason,
                updated_at=datetime.now(timezone.utc),
            )
            self._records[run_id] = record
            return record
