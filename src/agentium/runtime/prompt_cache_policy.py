"""Prompt caching policy and telemetry helpers."""

from __future__ import annotations

from typing import Dict

from pydantic import BaseModel, Field


class PromptCacheStats(BaseModel):
    """Cache statistics for one prompt request."""

    cache_key: str = Field(min_length=1)
    cache_hit: bool = False
    input_tokens_saved: int = Field(default=0, ge=0)
    latency_ms_saved: int = Field(default=0, ge=0)


class PromptCachePolicy:
    """Track prompt cache hit/miss metrics by stable cache key."""

    def __init__(self) -> None:
        self._seen_keys: Dict[str, int] = {}

    def record_request(
        self, cache_key: str, input_tokens: int, latency_ms: int
    ) -> PromptCacheStats:
        """Record one request and return cache impact metrics."""

        seen = self._seen_keys.get(cache_key, 0)
        self._seen_keys[cache_key] = seen + 1
        if seen == 0:
            return PromptCacheStats(
                cache_key=cache_key,
                cache_hit=False,
                input_tokens_saved=0,
                latency_ms_saved=0,
            )

        return PromptCacheStats(
            cache_key=cache_key,
            cache_hit=True,
            input_tokens_saved=max(0, input_tokens),
            latency_ms_saved=max(1, int(latency_ms * 0.5)),
        )

