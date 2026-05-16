"""Aggregation helpers for chat agent tool loop usage."""

from __future__ import annotations

from agentium.ai_gateway.deepseek_chat import LlmUsageSnapshot
from agentium.coordination.chat_agent_tool_loop import aggregate_llm_usage_snapshots


def test_aggregate_llm_usage_sums_rounds() -> None:
    agg = aggregate_llm_usage_snapshots(
        [
            LlmUsageSnapshot(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            LlmUsageSnapshot(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        ]
    )
    assert agg is not None
    assert agg.prompt_tokens == 11
    assert agg.completion_tokens == 22
    assert agg.total_tokens == 33


def test_aggregate_llm_usage_empty() -> None:
    assert aggregate_llm_usage_snapshots([None, None]) is None
