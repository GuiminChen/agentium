"""Tests for :mod:`agentium.governance.evolution_trajectory` sanitization."""

from __future__ import annotations

import pytest

from agentium.governance.evolution_plugin import TrajectoryBatch, TrajectoryEvent
from agentium.governance.evolution_trajectory import (
    MAX_TRAJECTORY_EVENTS,
    sanitize_trajectory_batch,
)


def test_sanitize_accepts_well_formed_batch() -> None:
    b = TrajectoryBatch(
        run_id="run-ok",
        events=[
            TrajectoryEvent(step_type="custom.observation", payload={"k": "v"}),
        ],
    )
    out = sanitize_trajectory_batch(b)
    assert out.run_id == "run-ok"
    assert len(out.events) == 1
    assert out.events[0].step_type == "custom.observation"


def test_sanitize_rejects_bad_step_type() -> None:
    b = TrajectoryBatch(
        run_id="r1",
        events=[TrajectoryEvent(step_type="9bad", payload={})],
    )
    with pytest.raises(ValueError, match="step_type"):
        sanitize_trajectory_batch(b)


def test_sanitize_rejects_too_many_events() -> None:
    evs = [
        TrajectoryEvent(step_type=f"e{i}.step", payload={}) for i in range(MAX_TRAJECTORY_EVENTS + 1)
    ]
    b = TrajectoryBatch(run_id="r2", events=evs)
    with pytest.raises(ValueError, match="at most"):
        sanitize_trajectory_batch(b)
