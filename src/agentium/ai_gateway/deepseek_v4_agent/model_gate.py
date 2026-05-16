"""Resolve whether the completion ``model`` id should receive DeepSeek-V4-only adaptations."""

from __future__ import annotations


def is_deepseek_v4_series_model(model_id: str | None) -> bool:
    """Return True when ``model_id`` is in the official ``deepseek-v4-*`` family.

    Examples that match: ``deepseek-v4-flash``, ``deepseek-v4-pro``. Non-V4 DeepSeek
    chat models (e.g. legacy ids) must not enable DSML appendix, Think Max injection,
    ``thinking`` envelope, or DSML tool-call synthesis.

    Args:
        model_id: Resolved OpenAI-compatible ``model`` string (request override or settings default).

    Returns:
        Whether V4 adapter paths should run for this completion.
    """

    normalized = (model_id or "").strip().lower()
    if not normalized:
        return False
    return normalized.startswith("deepseek-v4")
