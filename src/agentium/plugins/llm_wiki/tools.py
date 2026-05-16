"""Optional ``wiki_search`` tool when LLM-Wiki (crate) is enabled."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from agentium.shared.request_context import get_request_context
from agentium.tools.tool_registry import ToolRegistry, ToolSpec

if TYPE_CHECKING:
    from agentium.plugins.llm_wiki.service import LlmWikiPluginService


def _wiki_search_handler(service: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    q = str(args.get("query", "")).strip()
    if not q:
        return {"ok": False, "error": "missing_query"}
    ctx = get_request_context()
    scope = str(args.get("scope", "session")).strip().lower()
    if scope not in ("session", "tenant"):
        scope = "session"
    sess_override = str(args.get("session_id", "")).strip()
    effective_session = sess_override or (ctx.chat_session_id or "")
    lit = bool(args.get("literal", True))
    sem = bool(args.get("semantic", False))
    lim = args.get("limit", 10)
    try:
        limit = max(1, min(50, int(lim)))
    except (TypeError, ValueError):
        limit = 10
    violation = service.wiki_search_precheck(
        tenant_id=ctx.tenant_id,
        scope=scope,
        session_id=effective_session,
        wait_for_job_ids=args.get("wait_for_job_ids"),
    )
    if violation is not None:
        return {"ok": False, **violation}
    payload = service.host.search(
        ctx.tenant_id,
        q,
        literal=lit,
        semantic=sem,
        limit=limit,
        scope=scope,
        chat_session_id=effective_session,
    )
    return {"ok": True, "result": payload}


def register_llm_wiki_tools(
    registry: ToolRegistry,
    service: "LlmWikiPluginService | None",
) -> None:
    """Register wiki search tools (no-op when *service* is ``None``)."""

    if service is None:
        return

    def _handler(args: Dict[str, Any]) -> Dict[str, Any]:
        return _wiki_search_handler(service, args)

    registry.register(
        ToolSpec(
            name="wiki_search",
            capabilities=["wiki", "retrieval"],
            risk_level="low",
            handler=_handler,
            supply_origin="web",
        )
    )
