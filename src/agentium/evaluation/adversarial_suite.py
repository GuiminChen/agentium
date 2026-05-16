"""Adversarial / OOD evaluation fixtures with deterministic graders (P1-18)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Union

import yaml

FixtureInput = Union[str, Path, MutableMapping[str, Any]]


def default_adversarial_fixture_path() -> Path:
    """Repository fixture path for bundled adversarial samples."""

    return (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "eval"
        / "adversarial"
        / "samples.yaml"
    )


def load_adversarial_samples(fixture: Optional[FixtureInput] = None) -> List[Dict[str, Any]]:
    """Load adversarial sample definitions from YAML or an in-memory mapping.

    Args:
        fixture: Path/str to YAML, or a dict with key ``samples`` (list).

    Returns:
        List of sample dicts (copied shallowly from YAML).
    """

    if fixture is None:
        raw_path = default_adversarial_fixture_path()
        with raw_path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    elif isinstance(fixture, (str, Path)):
        with Path(fixture).expanduser().open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    else:
        data = dict(fixture)
    rows = data.get("samples")
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(dict(row))
    return out


def grade_adversarial_sample(sample: Mapping[str, Any], assistant_text: str) -> float:
    """Return 1.0 when ``assistant_text`` satisfies deterministic rules, else 0.0.

    Rules:
        - ``forbidden_substrings``: score 0 if any substring present (case-sensitive).
        - ``required_substrings``: score 0 if any substring missing.
    """

    hay = str(assistant_text)
    banned = sample.get("forbidden_substrings") or []
    if isinstance(banned, list):
        for token in banned:
            if str(token) and str(token) in hay:
                return 0.0
    required = sample.get("required_substrings") or []
    if isinstance(required, list):
        for token in required:
            if not token or str(token) not in hay:
                return 0.0
    return 1.0


def summarize_grades(
    samples: List[Mapping[str, Any]],
    assistant_text: str,
) -> Dict[str, Any]:
    """Grade all samples against a single assistant output (dry-run / harness hook)."""

    scores: Dict[str, float] = {}
    for sample in samples:
        sid = str(sample.get("id", "")).strip() or "unknown"
        scores[sid] = grade_adversarial_sample(sample, assistant_text)
    mean = sum(scores.values()) / len(scores) if scores else 0.0
    return {"per_sample": scores, "mean": mean, "count": len(scores)}
