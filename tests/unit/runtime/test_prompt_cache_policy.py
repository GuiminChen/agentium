from __future__ import annotations

from agentium.runtime.prompt_cache_policy import PromptCachePolicy


def test_prompt_cache_policy_tracks_miss_then_hit() -> None:
    policy = PromptCachePolicy()

    first = policy.record_request(
        cache_key="system:v1|tools:v2",
        input_tokens=1200,
        latency_ms=800,
    )
    second = policy.record_request(
        cache_key="system:v1|tools:v2",
        input_tokens=1200,
        latency_ms=800,
    )

    assert first.cache_hit is False
    assert first.input_tokens_saved == 0
    assert second.cache_hit is True
    assert second.input_tokens_saved == 1200
    assert second.latency_ms_saved > 0
