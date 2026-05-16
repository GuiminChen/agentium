"""Stable identifiers for deferred (async) task kinds (plugin registry keys)."""

from __future__ import annotations

# Chat maintenance
KIND_CHAT_GENERATE_SESSION_TITLE = "chat.generate_session_title"
KIND_LLM_WIKI_INGEST_COMPILE = "llm_wiki.ingest_compile"

DEFAULT_DEFERRED_LANE = "default"
LANE_CHAT = "chat"

__all__ = [
    "DEFAULT_DEFERRED_LANE",
    "KIND_LLM_WIKI_INGEST_COMPILE",
    "LANE_CHAT",
]
