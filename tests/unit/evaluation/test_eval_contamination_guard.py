from __future__ import annotations

from agentium.evaluation.eval_contamination_guard import EvalContaminationGuard


def test_eval_contamination_guard_flags_benchmark_key_leakage() -> None:
    guard = EvalContaminationGuard()

    result = guard.inspect_transcript(
        task_prompt="Solve this benchmark task.",
        transcript=(
            "The model found an answer key for BrowseComp on a public mirror "
            "and decoded all benchmark answers."
        ),
    )

    assert result.suspected is True
    assert "answer_key_reference" in result.reasons


def test_eval_contamination_guard_accepts_normal_transcript() -> None:
    guard = EvalContaminationGuard()

    result = guard.inspect_transcript(
        task_prompt="Implement retry logic.",
        transcript="The agent wrote tests, implemented retries, and passed unit checks.",
    )

    assert result.suspected is False
    assert result.reasons == []
