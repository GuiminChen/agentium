"""Unit tests for the in-process IPC bus."""

from __future__ import annotations

import pytest

from agentium.infra.mq.inproc_bus import InprocBus


def test_inproc_bus_delivers_to_all_subscribers():
    bus = InprocBus(history_size=4)
    received_a: list[dict] = []
    received_b: list[dict] = []
    bus.subscribe("topic", lambda m: received_a.append(dict(m.payload)))
    bus.subscribe("topic", lambda m: received_b.append(dict(m.payload)))

    bus.publish("topic", {"x": 1})
    assert received_a == [{"x": 1}]
    assert received_b == [{"x": 1}]
    counters = bus.counters()
    assert counters["published_total"] == 1
    assert counters["delivered_total"] == 2


def test_inproc_bus_isolates_subscriber_errors():
    seen_errors: list[BaseException] = []
    bus = InprocBus(on_error=lambda topic, msg, exc: seen_errors.append(exc))

    def bad(_msg):
        raise RuntimeError("boom")

    received: list[str] = []
    bus.subscribe("t", bad)
    bus.subscribe("t", lambda m: received.append(m.id))

    bus.publish("t", {"k": "v"})
    assert len(received) == 1
    assert isinstance(seen_errors[0], RuntimeError)
    assert bus.counters()["subscriber_errors_total"] == 1


def test_inproc_bus_replay_returns_history():
    bus = InprocBus(history_size=3)
    bus.publish("t", {"i": 1})
    bus.publish("t", {"i": 2})
    bus.publish("t", {"i": 3})
    bus.publish("t", {"i": 4})  # ring buffer drops 1
    payloads = [dict(m.payload)["i"] for m in bus.replay("t")]
    assert payloads == [2, 3, 4]


def test_inproc_bus_replay_filters_by_since():
    times = iter([1.0, 2.0, 3.0])
    bus = InprocBus(history_size=8, clock=lambda: next(times))
    bus.publish("t", {"i": 1})
    bus.publish("t", {"i": 2})
    bus.publish("t", {"i": 3})
    payloads = [dict(m.payload)["i"] for m in bus.replay("t", since=1.5)]
    assert payloads == [2, 3]


def test_inproc_bus_unsubscribe_stops_delivery():
    bus = InprocBus()
    received: list[dict] = []
    handle = bus.subscribe("t", lambda m: received.append(dict(m.payload)))
    bus.publish("t", {"i": 1})
    handle()
    bus.publish("t", {"i": 2})
    assert received == [{"i": 1}]


def test_inproc_bus_rejects_empty_topic():
    bus = InprocBus()
    with pytest.raises(ValueError):
        bus.publish("", {})
    with pytest.raises(ValueError):
        bus.subscribe("", lambda m: None)
