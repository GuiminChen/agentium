"""Unit tests for the eval runner."""

from __future__ import annotations

from agentium.evaluation.eval_runner import EvalSample, run_repeated_eval


def test_run_repeated_eval_basic() -> None:
    report = run_repeated_eval(
        metric_name="m",
        runner=lambda i: EvalSample(score=float(i)),
        repetitions=4,
    )
    assert report.repetitions == 4
    assert report.mean == 1.5
    assert report.ci95_low <= report.mean <= report.ci95_high


def test_run_repeated_eval_success_rate() -> None:
    report = run_repeated_eval(
        metric_name="m",
        runner=lambda i: EvalSample(score=1.0),
        repetitions=3,
        success_threshold=0.5,
    )
    assert report.success_rate == 1.0
