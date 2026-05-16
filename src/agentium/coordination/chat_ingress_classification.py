"""Deterministic ingress classification for TradeAgent-style chat messages.

Design intent: When ``auto_ingress`` is enabled on POST /v1/chat/messages, the
control plane derives ``message_disposition`` and ``mcp_execution_tier`` from
message content without an extra LLM call (v1 scope).
"""

from __future__ import annotations

import re
from typing import Tuple

from agentium.api.http.control_plane_schemas import MessageDisposition, McpExecutionTier

_STEER_PATTERN = re.compile(
    r"\b(steer|override|ignore\s+prior|forget\s+that|stop\s+doing)\b",
    re.IGNORECASE,
)
_FOLLOWUP_PATTERN = re.compile(
    r"\b(continue|go\s+on|and\s+then|next\s+step|what\s+about|also)\b",
    re.IGNORECASE,
)
_CODE_PATTERN = re.compile(
    r"\b(code\s+exec|sandbox|subprocess|compile\s+and\s+run|python\s+-c)\b",
    re.IGNORECASE,
)


def classify_chat_ingress(content: str) -> Tuple[MessageDisposition, McpExecutionTier]:
    """Return disposition and MCP tier from user-visible message text.

    Rules (v1):
        - Empty after strip defaults to collect + direct-tool.
        - Strong steer verbs → steer + direct-tool unless code-exec hints win.
        - Continuation / elaboration cues → followup when no steer match.
        - Code-exec language → code-exec-mcp tier (disposition unchanged by tier alone).
        - Very short prompts (< 12 chars) → followup when alphanumeric-heavy chat shorthand.

    Args:
        content: Raw user message body.

    Returns:
        Tuple of ``(message_disposition, mcp_execution_tier)``.
    """

    text = (content or "").strip()
    if not text:
        return "collect", "direct-tool"

    disposition: MessageDisposition = "collect"
    if _STEER_PATTERN.search(text):
        disposition = "steer"
    elif _FOLLOWUP_PATTERN.search(text) or len(text) < 12:
        disposition = "followup"

    tier: McpExecutionTier = "direct-tool"
    if _CODE_PATTERN.search(text):
        tier = "code-exec-mcp"

    return disposition, tier
