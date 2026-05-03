"""Unit tests for :class:`EventIngestor`."""

from __future__ import annotations

import itertools

import pytest

from agentium.background.event_ingestor import EventIngestor


def test_submit_returns_event_in_fifo_order() -> None:
    ingestor = EventIngestor()
    ingestor.submit("memory.fresh_item", {"tenant_id": "t1"})
    ingestor.submit("approval.expired", {"tenant_id": "t1"})

    events = ingestor.drain()
    assert [e.topic for e in events] == ["memory.fresh_item", "approval.expired"]
    assert ingestor.drain() == []


def test_dedupe_within_ttl_drops_duplicate() -> None:
    counter = itertools.count(start=0, step=10)

    def clock() -> float:
        return float(next(counter))

    ingestor = EventIngestor(dedupe_ttl_seconds=60.0, clock=clock)
    first = ingestor.submit("topic", {"a": 1}, dedupe_key="k1")
    second = ingestor.submit("topic", {"a": 2}, dedupe_key="k1")
    assert first is not None
    assert second is None
    assert len(ingestor) == 1


def test_dedupe_after_ttl_window_allows_again() -> None:
    times = iter([0.0, 100.0, 200.0])

    ingestor = EventIngestor(dedupe_ttl_seconds=10.0, clock=lambda: next(times))
    assert ingestor.submit("topic", {}, dedupe_key="k") is not None
    assert ingestor.submit("topic", {}, dedupe_key="k") is not None  # ttl expired


def test_max_events_drops_oldest() -> None:
    ingestor = EventIngestor(max_events=2)
    ingestor.submit("a", {})
    ingestor.submit("b", {})
    ingestor.submit("c", {})
    assert [e.topic for e in ingestor.peek()] == ["b", "c"]


def test_drain_with_limit_returns_partial() -> None:
    ingestor = EventIngestor()
    for i in range(5):
        ingestor.submit("t", {"i": i}, dedupe_key=str(i))
    first_two = ingestor.drain(max_events=2)
    assert len(first_two) == 2
    assert len(ingestor) == 3


def test_empty_topic_raises() -> None:
    with pytest.raises(ValueError):
        EventIngestor().submit("", {})
