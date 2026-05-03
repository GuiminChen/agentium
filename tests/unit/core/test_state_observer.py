"""Unit tests for the StateObserver health aggregator."""

from __future__ import annotations

from agentium.core.state_observer import HealthStatus, ProbeReport, StateObserver


def _probe(name: str, status: HealthStatus, **detail) -> ProbeReport:
    return ProbeReport(name=name, status=status, detail=detail)


def test_state_observer_aggregates_worst_status():
    observer = StateObserver(startup_grace_seconds=0.0)
    observer.register_probe("a", lambda: _probe("a", HealthStatus.HEALTHY))
    observer.register_probe("b", lambda: _probe("b", HealthStatus.DEGRADED))
    snap = observer.snapshot()
    assert snap.status == HealthStatus.DEGRADED
    assert snap.ready is True


def test_state_observer_marks_unready_on_unhealthy():
    observer = StateObserver(startup_grace_seconds=0.0)
    observer.register_probe("a", lambda: _probe("a", HealthStatus.UNHEALTHY))
    snap = observer.snapshot()
    assert snap.status == HealthStatus.UNHEALTHY
    assert snap.ready is False


def test_state_observer_isolates_probe_failures():
    observer = StateObserver(startup_grace_seconds=0.0)

    def boom() -> ProbeReport:
        raise RuntimeError("disk full")

    observer.register_probe("a", boom)
    observer.register_probe("b", lambda: _probe("b", HealthStatus.HEALTHY))
    snap = observer.snapshot()
    statuses = {p.name: p.status for p in snap.probes}
    assert statuses["a"] == HealthStatus.UNHEALTHY
    assert statuses["b"] == HealthStatus.HEALTHY
    assert snap.status == HealthStatus.UNHEALTHY


def test_state_observer_increments_counters():
    observer = StateObserver(startup_grace_seconds=0.0)
    observer.register_probe("a", lambda: _probe("a", HealthStatus.UNHEALTHY))
    observer.snapshot()
    counters = observer.counters()
    assert counters["snapshots_total"] == 1
    assert counters["unhealthy_total"] == 1
