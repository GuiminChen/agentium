from __future__ import annotations

import pytest

from agentium.core.agent_lifecycle import (
    AgentLifecycleError,
    AgentLifecycleManager,
    AgentState,
)
from agentium.models.context import RequestContext


def _context(run_id: str = "run-life-1") -> RequestContext:
    return RequestContext(
        request_id="req-life-1",
        run_id=run_id,
        tenant_id="tenant-a",
        user_id="user-a",
        trace_id="trace-life-1",
    )


def test_agent_lifecycle_tracks_os_style_states() -> None:
    manager = AgentLifecycleManager()
    context = _context()

    assert manager.create(context).state == AgentState.CREATED
    assert manager.ready(context.run_id).state == AgentState.READY
    assert manager.start(context.run_id).state == AgentState.RUNNING
    assert manager.block_hitl(context.run_id, "approval required").state == AgentState.BLOCKED_HITL
    assert manager.resume(context.run_id).state == AgentState.READY
    assert manager.start(context.run_id).state == AgentState.RUNNING
    assert manager.stop(context.run_id).state == AgentState.STOPPED
    assert manager.cleanup(context.run_id).state == AgentState.CLEANED


def test_agent_lifecycle_rejects_invalid_transition() -> None:
    manager = AgentLifecycleManager()
    context = _context()
    manager.create(context)

    with pytest.raises(AgentLifecycleError):
        manager.start(context.run_id)


def test_agent_lifecycle_force_kill_is_explicit() -> None:
    manager = AgentLifecycleManager()
    context = _context()
    manager.create(context)
    manager.ready(context.run_id)
    manager.start(context.run_id)

    record = manager.kill(context.run_id, "timeout")

    assert record.state == AgentState.KILLED
    assert record.reason == "timeout"
