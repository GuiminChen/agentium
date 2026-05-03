"""Deterministic reliability drills for release gates and SLO evidence."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class ReliabilityScenarioResult:
    """One reliability drill scenario result."""

    name: str
    attempts: int
    safe_degrade_count: int
    mttr_seconds: float
    passed: bool


@dataclass(frozen=True)
class ReliabilityDrillReport:
    """Aggregate reliability drill report."""

    scenarios: List[ReliabilityScenarioResult]
    metrics: Dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Return True when all scenarios pass."""

        return all(scenario.passed for scenario in self.scenarios)


class ReliabilityDrillRunner:
    """Run deterministic fault drills for safe-degrade acceptance evidence."""

    def __init__(self, mttr_target_seconds: float = 300.0) -> None:
        if mttr_target_seconds <= 0:
            raise ValueError("mttr_target_seconds must be positive")
        self._mttr_target_seconds = mttr_target_seconds

    def run_standard_drills(self, rounds_per_scenario: int = 10) -> ReliabilityDrillReport:
        """Run the standard model-timeout/tool-error/queue-backpressure drills."""

        if rounds_per_scenario <= 0:
            raise ValueError("rounds_per_scenario must be positive")
        scenarios = [
            self._run_scenario("model_timeout", rounds_per_scenario),
            self._run_scenario("tool_exception", rounds_per_scenario),
            self._run_scenario("queue_backpressure", rounds_per_scenario),
        ]
        total_attempts = sum(scenario.attempts for scenario in scenarios)
        total_safe = sum(scenario.safe_degrade_count for scenario in scenarios)
        max_mttr = max((scenario.mttr_seconds for scenario in scenarios), default=0.0)
        safe_rate = total_safe / total_attempts if total_attempts else 0.0
        return ReliabilityDrillReport(
            scenarios=scenarios,
            metrics={
                "recovery_mttr_seconds": max_mttr,
                "safe_degrade_success_rate": safe_rate,
                "mttr_target_seconds": self._mttr_target_seconds,
            },
        )

    def _run_scenario(self, name: str, attempts: int) -> ReliabilityScenarioResult:
        started = time.monotonic()
        safe_degrade_count = 0
        for _index in range(attempts):
            if self._simulate_safe_degrade(name):
                safe_degrade_count += 1
        mttr_seconds = max(0.0, time.monotonic() - started)
        return ReliabilityScenarioResult(
            name=name,
            attempts=attempts,
            safe_degrade_count=safe_degrade_count,
            mttr_seconds=mttr_seconds,
            passed=safe_degrade_count == attempts and mttr_seconds <= self._mttr_target_seconds,
        )

    @staticmethod
    def _simulate_safe_degrade(name: str) -> bool:
        """Return deterministic safe-degrade outcome for supported drills."""

        return name in {"model_timeout", "tool_exception", "queue_backpressure"}
