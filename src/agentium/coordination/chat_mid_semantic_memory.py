"""Mem0-style mid/long chat memories: scoped LLM extraction + Hermes-like session summary.

SHORT-layer excerpts remain verbatim traces in :mod:`chat_turn_service`. This module adds:

- **Scoped facts** — ``memory_scope`` = ``session`` (MID, tied to ``run_id``) vs ``user``
  (LONG, cross-session within tenant + ``user_id``).
- **Running summary** — structured Markdown digest for the current turn pair (MID),
  inspired by Hermes context-compression section templates.

Sources: Mem0 scoping (`user_id` / ``run_id``); Hermes structured compaction headings.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Literal, Tuple

import structlog

from agentium.ai_gateway.deepseek_chat import DeepSeekChatCompletionClient, DeepSeekChatCompletionError

_LOGGER = structlog.get_logger(__name__)

MemoryScope = Literal["session", "user"]

_MAX_USER_CHARS = 8000
_MAX_ASSISTANT_CHARS = 12000
_MAX_FACT_CHARS = 512
_MAX_FACTS_PER_TURN = 8
_MAX_SUMMARY_CHARS = 6000

_MEM_EXTRACTION_SYSTEM = (
    "You extract portable memories from ONE chat turn (Mem0-style). "
    'Return ONLY valid JSON: {"memories":[{"text":"...","scope":"session"|"user"}, ...]}.\n'
    "Rules:\n"
    '- ``scope`` = ``user`` ONLY for durable facts about this human that should apply across '
    "future chat sessions (preferences, identity, standing commitments). Prefer ``session`` "
    "for task-specific progress, one-off questions, or ephemeral context.\n"
    "- Each ``text`` is a short declarative fact; no verbatim dialogue dumps.\n"
    "- If nothing matters, return {\"memories\":[]}.\n"
    "- Max "
    + str(_MAX_FACTS_PER_TURN)
    + " objects; each ``text`` max "
    + str(_MAX_FACT_CHARS)
    + " characters.\n"
)

_SUMMARY_SYSTEM = (
    "You summarize ONE user↔assistant exchange into running session context for the coding/agent "
    "assistant (Hermes-style headings). Output ONLY Markdown with exactly these sections "
    "(use ### headings, omit empty sections):\n"
    "### Goal\n### Constraints & Preferences\n### Progress\n### Key Decisions\n"
    "### Relevant Artifacts\n### Next Steps\n### Critical Context\n"
    "Be concise; prefer bullets. If a section has nothing relevant, write \"(none)\".\n"
)


def _clip(text: str, cap: int) -> str:
    t = (text or "").strip()
    if len(t) <= cap:
        return t
    return t[: cap - 1] + "…"


def normalize_fact_key(text: str) -> str:
    """Collapse whitespace for dedupe comparisons."""

    return " ".join((text or "").strip().lower().split())


def stable_fact_hash(session_id: str, normalized_fact: str) -> str:
    """Stable record key fragment for session-scoped facts."""

    blob = f"{session_id}\n{normalized_fact}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


def stable_user_fact_hash(tenant_id: str, user_id: str, normalized_fact: str) -> str:
    """Stable key fragment for user-scoped (LONG) facts."""

    blob = f"{tenant_id}\n{user_id}\n{normalized_fact}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


def parse_scoped_memories_json(raw_text: str) -> List[Tuple[str, MemoryScope]]:
    """Parse model JSON into (text, scope) pairs."""

    blob = (raw_text or "").strip()
    if not blob:
        return []
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", blob, re.DOTALL | re.IGNORECASE)
    if fence:
        blob = fence.group(1).strip()
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    raw_list = data.get("memories")
    if not isinstance(raw_list, list):
        return []
    out: List[Tuple[str, MemoryScope]] = []
    for item in raw_list:
        scope: MemoryScope = "session"
        if isinstance(item, str):
            piece = _clip(item, _MAX_FACT_CHARS)
            if piece:
                out.append((piece, "session"))
        elif isinstance(item, dict):
            txt = item.get("text")
            if not isinstance(txt, str):
                continue
            piece = _clip(txt, _MAX_FACT_CHARS)
            if not piece:
                continue
            raw_scope = str(item.get("scope") or "session").strip().lower()
            if raw_scope == "user":
                scope = "user"
            else:
                scope = "session"
            out.append((piece, scope))
        if len(out) >= _MAX_FACTS_PER_TURN:
            break
    return out


def extract_mid_term_memories(
    deepseek: DeepSeekChatCompletionClient,
    *,
    user_turn_text: str,
    assistant_turn_text: str,
    trace_id: str,
    request_id: str,
    model_override: str | None,
) -> List[Tuple[str, MemoryScope]]:
    """Run one completion to extract scoped memory tuples."""

    user_c = _clip(user_turn_text, _MAX_USER_CHARS)
    asst_c = _clip(assistant_turn_text, _MAX_ASSISTANT_CHARS)
    if not user_c and not asst_c:
        return []
    payload_user = json.dumps(
        {"user_message": user_c, "assistant_message": asst_c},
        ensure_ascii=False,
    )
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": _MEM_EXTRACTION_SYSTEM},
        {"role": "user", "content": payload_user},
    ]
    try:
        result = deepseek.complete_chat(
            messages,
            trace_id=trace_id,
            request_id=request_id,
            thinking=None,
            model_override=model_override,
        )
    except DeepSeekChatCompletionError as exc:
        _LOGGER.warning(
            "chat_mid_semantic_extract_llm_failed",
            trace_id=trace_id,
            request_id=request_id,
            error=str(exc),
        )
        return []
    return parse_scoped_memories_json(result.text)


def extract_session_running_summary(
    deepseek: DeepSeekChatCompletionClient,
    *,
    user_turn_text: str,
    assistant_turn_text: str,
    trace_id: str,
    request_id: str,
    model_override: str | None,
) -> str:
    """Hermes-style structured Markdown summary for one exchange."""

    user_c = _clip(user_turn_text, _MAX_USER_CHARS)
    asst_c = _clip(assistant_turn_text, _MAX_ASSISTANT_CHARS)
    if not user_c and not asst_c:
        return ""
    bundle = json.dumps(
        {"user_message": user_c, "assistant_message": asst_c},
        ensure_ascii=False,
    )
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": bundle},
    ]
    try:
        result = deepseek.complete_chat(
            messages,
            trace_id=trace_id,
            request_id=request_id,
            thinking=None,
            model_override=model_override,
        )
    except DeepSeekChatCompletionError as exc:
        _LOGGER.warning(
            "chat_session_summary_llm_failed",
            trace_id=trace_id,
            request_id=request_id,
            error=str(exc),
        )
        return ""
    return _clip(result.text.strip(), _MAX_SUMMARY_CHARS)


__all__ = [
    "MemoryScope",
    "extract_mid_term_memories",
    "extract_session_running_summary",
    "normalize_fact_key",
    "parse_scoped_memories_json",
    "stable_fact_hash",
    "stable_user_fact_hash",
]
