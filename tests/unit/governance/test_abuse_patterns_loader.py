"""Abuse patterns loader (P1-9)."""

from __future__ import annotations

from pathlib import Path

from agentium.governance.abuse_patterns.loader import load_abuse_patterns, match_abuse_patterns


def test_load_default_abuse_patterns() -> None:
    bundled = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "agentium"
        / "governance"
        / "abuse_patterns"
        / "bundled"
        / "default_patterns.yaml"
    )
    patterns = load_abuse_patterns(bundled)
    assert len(patterns) >= 1
    hit = match_abuse_patterns("prefix AKIA9EXAMPLE suffix", patterns)
    assert "aws_key_heuristic" in hit
