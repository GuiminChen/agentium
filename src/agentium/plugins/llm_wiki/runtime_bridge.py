"""Process-global handle for :class:`LlmWikiPluginService` (deferred worker bridge)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from agentium.plugins.llm_wiki.service import LlmWikiPluginService

_bridge: Optional["LlmWikiPluginService"] = None


def set_llm_wiki_service(svc: Optional["LlmWikiPluginService"]) -> None:
    """Install or clear the active service (bootstrap / shutdown)."""

    global _bridge
    _bridge = svc


def get_llm_wiki_service() -> Optional["LlmWikiPluginService"]:
    return _bridge
