"""Unit tests for EmergenceGuardrails circuit breakers."""

from __future__ import annotations

from agentium.coordination.emergence_guardrails import (
    EmergenceGuardrails,
    GuardrailLimit,
    GuardrailState,
)


def test_guardrails_pass_then_warn_then_trip():
    g = EmergenceGuardrails(
        limits={"channel.outbound": GuardrailLimit(warn_threshold=2, hard_limit=3)}
    )
    decisions = [
        g.try_increment("channel.outbound", "t").state for _ in range(3)
    ]
    assert decisions == [GuardrailState.OK, GuardrailState.WARN, GuardrailState.WARN]
    tripped = g.try_increment("channel.outbound", "t")
    assert tripped.state == GuardrailState.TRIPPED


def test_guardrails_isolate_tenants():
    g = EmergenceGuardrails(
        limits={"channel.outbound": GuardrailLimit(warn_threshold=1, hard_limit=1)}
    )
    g.try_increment("channel.outbound", "t-a")
    other = g.try_increment("channel.outbound", "t-b")
    assert other.state == GuardrailState.WARN


def test_guardrails_window_drops_old_events():
    fake_time = [0.0]
    g = EmergenceGuardrails(
        limits={
            "channel.outbound": GuardrailLimit(
                warn_threshold=2, hard_limit=2, window_seconds=10
            )
        },
        clock=lambda: fake_time[0],
    )
    g.try_increment("channel.outbound", "t")
    g.try_increment("channel.outbound", "t")
    fake_time[0] = 100.0
    again = g.try_increment("channel.outbound", "t")
    assert again.state == GuardrailState.OK
    assert again.current == 1


def test_guardrails_unknown_counter_passes():
    g = EmergenceGuardrails(limits={})
    assert g.try_increment("anything", "t").state == GuardrailState.OK


def test_guardrails_invoke_on_trip_callback():
    seen = []
    g = EmergenceGuardrails(
        limits={"x": GuardrailLimit(warn_threshold=0, hard_limit=1)},
        on_trip=seen.append,
    )
    g.try_increment("x", "t")
    g.try_increment("x", "t")
    assert len(seen) == 1 and seen[0].state == GuardrailState.TRIPPED
