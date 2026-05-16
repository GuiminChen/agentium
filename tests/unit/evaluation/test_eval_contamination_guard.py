"""Tests for evaluation contamination guard."""

from __future__ import annotations

import pytest

from agentium.evaluation.eval_contamination_guard import EvalContaminationError, EvalContaminationGuard


def test_browsecomp_keyword_flags() -> None:
    g = EvalContaminationGuard()
    r = g.inspect_transcript("t", "mention BrowseComp dataset")
    assert r.suspected
    assert any("benchmark" in x for x in r.reasons)


def test_fail_hard_raises() -> None:
    g = EvalContaminationGuard()
    with pytest.raises(EvalContaminationError) as exc:
        g.inspect_transcript("t", "browsecomp answers here", fail_hard=True)
    assert exc.value.reasons
