"""Map client-facing reasoning effort strings to DeepSeek API ``reasoning_effort`` values."""

from __future__ import annotations

from typing import Literal

ReasoningEffortApi = Literal["high", "max"]


def normalize_reasoning_effort(raw: str) -> ReasoningEffortApi:
    """Compatibility mapping per DeepSeek thinking-mode docs (low/medium→high, xhigh→max)."""

    key = (raw or "").strip().lower()
    if key in {"max", "xhigh"}:
        return "max"
    if key in {"", "high", "low", "medium"}:
        return "high"
    # Unknown tokens default to high to avoid hard failures.
    return "high"
