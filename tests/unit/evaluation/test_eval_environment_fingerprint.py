"""Eval environment fingerprint helper."""

from __future__ import annotations

from agentium.evaluation.eval_environment_fingerprint import build_eval_environment_fingerprint


def test_fingerprint_has_core_keys() -> None:
    fp = build_eval_environment_fingerprint(include_agentium_env=False)
    assert "python_version" in fp
    assert "platform" in fp
    assert "cpu_count" in fp
    assert isinstance(fp["agentium_env"], dict)
