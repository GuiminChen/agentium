"""Lightweight runtime health/state observer.

Mirrors PRD §3.16 / docker-design ``healthcheck/restart`` semantics: components
register *probes* that report ``healthy``/``degraded``/``unhealthy`` together
with a structured detail payload; the observer aggregates the most severe
status and exposes counters that the HTTP control plane forwards on
``/v1/healthz`` and ``/v1/readyz``.

The probes run synchronously when ``snapshot()`` is called.  Long-running
checks should be wrapped in their own caches.  The observer is intentionally
process-local; clustering is the IPC bus's responsibility.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Mapping, Optional


class HealthStatus(str, Enum):
    """Severity ordering used by :func:`HealthStatus.worst`."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"

    @classmethod
    def worst(cls, statuses: List["HealthStatus"]) -> "HealthStatus":
        """Return the most severe status (UNHEALTHY > DEGRADED > HEALTHY)."""

        order = {cls.HEALTHY: 0, cls.DEGRADED: 1, cls.UNHEALTHY: 2}
        if not statuses:
            return cls.HEALTHY
        worst_status = statuses[0]
        for status in statuses[1:]:
            if order[status] > order[worst_status]:
                worst_status = status
        return worst_status


@dataclass
class ProbeReport:
    """Result returned by a single probe."""

    name: str
    status: HealthStatus
    detail: Mapping[str, object] = field(default_factory=dict)
    error: Optional[str] = None


ProbeFn = Callable[[], ProbeReport]


@dataclass
class HealthSnapshot:
    """Aggregate snapshot returned by :meth:`StateObserver.snapshot`."""

    status: HealthStatus
    ready: bool
    started_at: float
    observed_at: float
    probes: List[ProbeReport]


class StateObserver:
    """Aggregates probes and exposes ready/health summaries.

    Args:
        clock: monotonic-ish clock; defaults to :func:`time.time`.
        startup_grace_seconds: window after construction during which any
            ``UNHEALTHY`` probe still reports ``ready=False`` instead of being
            promoted to ``DEGRADED``.  This avoids flapping during boot.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        startup_grace_seconds: float = 5.0,
    ) -> None:
        self._clock = clock
        self._started_at = clock()
        self._grace = startup_grace_seconds
        self._probes: Dict[str, ProbeFn] = {}
        self._lock = threading.RLock()
        self._counters: Dict[str, int] = {
            "snapshots_total": 0,
            "unhealthy_total": 0,
            "degraded_total": 0,
        }

    def register_probe(self, name: str, probe: ProbeFn) -> None:
        """Register or replace a probe by name."""

        if not name:
            raise ValueError("probe name must not be empty")
        with self._lock:
            self._probes[name] = probe

    def unregister_probe(self, name: str) -> None:
        with self._lock:
            self._probes.pop(name, None)

    def counters(self) -> Mapping[str, int]:
        with self._lock:
            return dict(self._counters)

    def snapshot(self) -> HealthSnapshot:
        """Run all probes and return aggregated health/ready info."""

        reports: List[ProbeReport] = []
        with self._lock:
            probes = list(self._probes.items())
        for name, probe in probes:
            try:
                report = probe()
                if not isinstance(report, ProbeReport):
                    raise TypeError(
                        f"probe {name!r} returned {type(report).__name__}, expected ProbeReport"
                    )
            except Exception as exc:
                report = ProbeReport(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    detail={},
                    error=str(exc),
                )
            reports.append(report)
        statuses = [r.status for r in reports]
        worst = HealthStatus.worst(statuses)

        observed_at = self._clock()
        ready = worst != HealthStatus.UNHEALTHY
        if not ready and (observed_at - self._started_at) <= self._grace:
            ready = False

        with self._lock:
            self._counters["snapshots_total"] += 1
            if worst == HealthStatus.UNHEALTHY:
                self._counters["unhealthy_total"] += 1
            elif worst == HealthStatus.DEGRADED:
                self._counters["degraded_total"] += 1

        return HealthSnapshot(
            status=worst,
            ready=ready,
            started_at=self._started_at,
            observed_at=observed_at,
            probes=reports,
        )
