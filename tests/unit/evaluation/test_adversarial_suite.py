"""Unit tests for P1-18 adversarial fixture loaders and graders."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.evaluation.adversarial_suite import (
    grade_adversarial_sample,
    load_adversarial_samples,
    summarize_grades,
)
from agentium.evaluation.eval_runner import EvalSample, run_repeated_eval


@pytest.mark.adversarial_eval
def test_load_default_adversarial_samples() -> None:
    rows = load_adversarial_samples()
    assert len(rows) >= 4
    ids = {str(r.get("id")) for r in rows}
    assert "leak-guard-001" in ids


def test_grade_forbidden_substrings() -> None:
    sample = {"id": "x", "forbidden_substrings": ["BAD"]}
    assert grade_adversarial_sample(sample, "ok") == 1.0
    assert grade_adversarial_sample(sample, "BAD") == 0.0


def test_grade_required_substrings() -> None:
    sample = {"id": "y", "required_substrings": ["[eval_safe_anchor]"]}
    assert grade_adversarial_sample(sample, "x [eval_safe_anchor] z") == 1.0
    assert grade_adversarial_sample(sample, "none") == 0.0


@pytest.mark.adversarial_eval
def test_summarize_grades_mean_on_clean_text() -> None:
    rows = load_adversarial_samples()
    text = "safe [eval_safe_anchor]"
    report = summarize_grades(rows, text)
    assert report["count"] == len(rows)
    assert report["mean"] == 1.0


@pytest.mark.adversarial_eval
def test_run_repeated_eval_with_adversarial_grader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = load_adversarial_samples()

    def _runner(_i: int) -> EvalSample:
        assistant = "safe [eval_safe_anchor]"
        mean = summarize_grades(rows, assistant)["mean"]
        return EvalSample(score=mean, metadata={"fixture": str(tmp_path)})

    rep = run_repeated_eval("adversarial_mean", _runner, repetitions=3)
    assert rep.mean == 1.0
