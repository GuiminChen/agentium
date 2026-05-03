"""Unit tests for PromptOutputPolicy enforcement."""

from __future__ import annotations

import pytest

from agentium.ai_gateway.prompt_output_policy import (
    PromptOutputPolicy,
    PromptOutputPolicyError,
    assert_prompt_complies,
    assert_response_complies,
    explain,
)


def test_prompt_too_long_is_rejected():
    policy = PromptOutputPolicy(max_prompt_chars=10)
    with pytest.raises(PromptOutputPolicyError) as exc:
        assert_prompt_complies("a" * 11, policy)
    assert exc.value.location == "prompt"
    assert "prompt_too_long" in exc.value.reason


def test_banned_substring_is_rejected():
    policy = PromptOutputPolicy(
        max_prompt_chars=1000, banned_prompt_substrings=("AKIA[A-Z0-9]{8,}",)
    )
    with pytest.raises(PromptOutputPolicyError):
        assert_prompt_complies("token AKIAABCDEFGH", policy)


def test_required_response_prefix_is_enforced():
    policy = PromptOutputPolicy(required_response_prefixes=("{",))
    with pytest.raises(PromptOutputPolicyError) as exc:
        assert_response_complies("not json", policy)
    assert "missing_required_prefix" in exc.value.reason


def test_pii_response_is_rejected_when_enabled():
    policy = PromptOutputPolicy(ban_personally_identifiable=True)
    with pytest.raises(PromptOutputPolicyError):
        assert_response_complies("contact me at user@example.com", policy)


def test_explain_collects_findings_without_raising():
    policy = PromptOutputPolicy(max_prompt_chars=2, max_response_chars=2)
    summary = explain("hello", "world", policy)
    assert "prompt_too_long" in summary["prompt"]
    assert "response_too_long" in summary["response"]
