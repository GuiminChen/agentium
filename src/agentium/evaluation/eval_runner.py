"""Reproducible evaluation runner with N repetitions and CI95."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class EvalSample:
    """One evaluation sample produced by a single run."""

    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalReport:
    """Aggregate report over N repetitions."""

    metric_name: str
    samples: List[EvalSample]
    repetitions: int
    mean: float
    std: float
    ci95_low: float
    ci95_high: float
    success_rate: float


def run_repeated_eval(
    metric_name: str,
    runner: Callable[[int], EvalSample],
    repetitions: int = 5,
    success_threshold: Optional[float] = None,
    environment_fingerprint: Optional[Dict[str, Any]] = None,
) -> EvalReport:
    """Run ``runner`` N times, returning aggregate metrics with CI95.

    Args:
        metric_name: Logical name of the metric being measured.
        runner: Callable invoked with the iteration index, returning EvalSample.
        repetitions: Number of repetitions (must be >= 2 for CI).
        success_threshold: Optional threshold; success_rate counts samples >=.
        environment_fingerprint: When set, copied into every sample's ``metadata`` under
            ``eval_environment_fingerprint`` for reproducibility reporting.
    """

    if repetitions < 1:
        raise ValueError("repetitions must be >= 1")
    samples: List[EvalSample] = []
    for index in range(repetitions):
        sample = runner(index)
        if not isinstance(sample, EvalSample):
            raise TypeError("runner must return EvalSample")
        md = dict(sample.metadata)
        if environment_fingerprint is not None:
            md["eval_environment_fingerprint"] = environment_fingerprint
        samples.append(EvalSample(score=sample.score, metadata=md))
    scores = [s.score for s in samples]
    mean = sum(scores) / len(scores)
    if len(scores) >= 2:
        std = statistics.pstdev(scores)
        sem = std / math.sqrt(len(scores))
        margin = 1.96 * sem
    else:
        std = 0.0
        margin = 0.0
    ci95_low = mean - margin
    ci95_high = mean + margin
    success_rate = (
        sum(1 for s in scores if s >= success_threshold) / len(scores)
        if success_threshold is not None
        else 0.0
    )
    return EvalReport(
        metric_name=metric_name,
        samples=samples,
        repetitions=len(samples),
        mean=mean,
        std=std,
        ci95_low=ci95_low,
        ci95_high=ci95_high,
        success_rate=success_rate,
    )
