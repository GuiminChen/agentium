from __future__ import annotations

import pytest

from agentium.reliability.drill_runner import ReliabilityDrillRunner


@pytest.mark.paper
def test_reliability_drill_runner_reports_mttr_and_safe_degrade() -> None:
    """Paper H6: fault drills converge within MTTR target under safe-degrade."""

    runner = ReliabilityDrillRunner(mttr_target_seconds=300.0)

    report = runner.run_standard_drills(rounds_per_scenario=2)

    assert report.passed is True
    assert report.metrics["recovery_mttr_seconds"] <= 300.0
    assert report.metrics["safe_degrade_success_rate"] == 1.0
    scenario_names = {scenario.name for scenario in report.scenarios}
    assert "model_timeout" in scenario_names
    assert "tool_exception" in scenario_names
    assert "queue_backpressure" in scenario_names
