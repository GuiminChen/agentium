"""Leakage filter that drops contaminated samples from main eval results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from agentium.evaluation.eval_contamination_guard import (
    EvalContaminationGuard,
    EvalContaminationResult,
)


@dataclass
class FilteredSample:
    """One eval sample with optional contamination report attached."""

    sample_id: str
    transcript: str
    contamination: Optional[EvalContaminationResult] = None
    excluded: bool = False


@dataclass
class LeakageFilterReport:
    """Aggregate report after filtering an eval sample stream."""

    total: int
    excluded: int
    kept: int
    excluded_ids: List[str] = field(default_factory=list)


def filter_leaked_samples(
    samples: Iterable[FilteredSample],
    guard: Optional[EvalContaminationGuard] = None,
) -> LeakageFilterReport:
    """Run contamination guard over samples and exclude leakage suspects."""

    guard = guard or EvalContaminationGuard()
    total = 0
    excluded = 0
    excluded_ids: List[str] = []
    for sample in samples:
        total += 1
        result = guard.inspect_transcript("", sample.transcript)
        sample.contamination = result
        if result.suspected:
            sample.excluded = True
            excluded += 1
            excluded_ids.append(sample.sample_id)
    return LeakageFilterReport(
        total=total,
        excluded=excluded,
        kept=total - excluded,
        excluded_ids=excluded_ids,
    )
