"""Unit tests for :class:`TriggerPlanner`."""

from __future__ import annotations

from agentium.background.event_ingestor import IngestedEvent
from agentium.background.trigger_planner import TriggerPlanner, TriggerRule


def _event(topic: str, payload: dict) -> IngestedEvent:
    return IngestedEvent(
        topic=topic,
        payload=payload,
        received_at=0.0,
    )


def test_low_risk_event_emits_suggestion() -> None:
    planner = TriggerPlanner(
        [TriggerRule(topic="memory.fresh", action="notify_memory", risk="low")]
    )
    result = planner.plan([_event("memory.fresh", {"tenant_id": "t1", "title": "x"})])
    assert len(result.suggestions) == 1
    assert result.suggestions[0].action == "notify_memory"
    assert not result.suggestions[0].requires_approval
    assert result.approval_required == []


def test_high_risk_event_requires_approval() -> None:
    planner = TriggerPlanner(
        [TriggerRule(topic="channel.failed", action="escalate", risk="high")]
    )
    result = planner.plan([_event("channel.failed", {"tenant_id": "t1"})])
    assert result.suggestions == []
    assert len(result.approval_required) == 1
    assert result.approval_required[0].requires_approval is True


def test_unknown_topic_is_ignored() -> None:
    planner = TriggerPlanner([])
    result = planner.plan([_event("unknown", {"tenant_id": "t1"})])
    assert result.suggestions == []
    assert result.approval_required == []


def test_event_without_tenant_is_skipped() -> None:
    planner = TriggerPlanner([TriggerRule(topic="t", action="a", risk="low")])
    result = planner.plan([_event("t", {})])
    assert result.suggestions == []


def test_payload_keys_filter_only_declared_fields() -> None:
    planner = TriggerPlanner(
        [
            TriggerRule(
                topic="t",
                action="a",
                payload_keys=("title",),
            )
        ]
    )
    result = planner.plan(
        [_event("t", {"tenant_id": "t1", "title": "hello", "secret": "drop"})]
    )
    assert "title" in result.suggestions[0].payload
    assert "secret" not in result.suggestions[0].payload
