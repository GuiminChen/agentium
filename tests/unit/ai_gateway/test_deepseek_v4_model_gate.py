"""Unit tests for DeepSeek-V4 model-id gating."""

from __future__ import annotations

import pytest

from agentium.ai_gateway.deepseek_v4_agent.model_gate import is_deepseek_v4_series_model


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("deepseek-v4-flash", True),
        ("deepseek-v4-pro", True),
        ("DeepSeek-V4-Pro", True),
        ("deepseek-v4-preview-x", True),
        ("deepseek-chat", False),
        ("deepseek-reasoner", False),
        ("gpt-4", False),
        ("", False),
        (None, False),
    ],
)
def test_is_deepseek_v4_series_model(model_id: str | None, expected: bool) -> None:
    assert is_deepseek_v4_series_model(model_id) is expected
