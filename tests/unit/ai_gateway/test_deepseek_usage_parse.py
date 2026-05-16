"""Tests for OpenAI-style ``usage`` extraction from completion JSON."""

from __future__ import annotations

from agentium.ai_gateway.deepseek_chat import parse_llm_usage_from_completion_json


def test_parse_llm_usage_full_shape() -> None:
    snap = parse_llm_usage_from_completion_json(
        {"usage": {"prompt_tokens": 3, "completion_tokens": 7, "total_tokens": 10}}
    )
    assert snap is not None
    assert snap.prompt_tokens == 3
    assert snap.completion_tokens == 7
    assert snap.total_tokens == 10


def test_parse_llm_usage_partial_total_only() -> None:
    snap = parse_llm_usage_from_completion_json({"usage": {"total_tokens": 42}})
    assert snap is not None
    assert snap.total_tokens == 42
    assert snap.prompt_tokens is None


def test_parse_llm_usage_missing() -> None:
    assert parse_llm_usage_from_completion_json({}) is None
    assert parse_llm_usage_from_completion_json({"usage": "bad"}) is None
