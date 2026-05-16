"""Classifier pipeline ordering helper (P1-10 / P1-11)."""

from __future__ import annotations

from agentium.security.classifier_pipeline import normalize_classifier_stages


def test_normalize_classifier_stages_trims_empty() -> None:
    assert normalize_classifier_stages(["a", "  ", "b"]) == ["a", "b"]
