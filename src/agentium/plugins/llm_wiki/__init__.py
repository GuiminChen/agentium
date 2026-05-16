"""LLM-Wiki plugin package (crate-backed)."""

from agentium.plugins.llm_wiki.service import (
    LlmWikiPluginService,
    build_llm_wiki_plugin_service,
)

__all__ = ["LlmWikiPluginService", "build_llm_wiki_plugin_service"]
