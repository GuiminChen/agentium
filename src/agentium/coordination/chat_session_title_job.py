"""Generate a concise chat session title from the first turn (async worker path)."""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, Optional

import structlog

from agentium.ai_gateway.deepseek_chat import DeepSeekChatCompletionClient
from agentium.app.settings import AppSettings, load_settings
from agentium.infra.db.sqlite_chat_session_store import SqliteChatSessionStore

_LOGGER = structlog.get_logger(__name__)

_TITLE_MAX_LEN = 120
_EXCERPT_CAP = 1800


def _deepseek_client(settings: AppSettings) -> Optional[DeepSeekChatCompletionClient]:
    if not settings.deepseek_api_key:
        return None
    return DeepSeekChatCompletionClient(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.chat_completion_model,
        timeout_seconds=float(settings.chat_completion_timeout_seconds),
    )


def clip_excerpt(text: str, cap: int = _EXCERPT_CAP) -> str:
    """Trim whitespace and bound excerpt length for LLM input."""

    cleaned = (text or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    if len(cleaned) <= cap:
        return cleaned
    return cleaned[:cap].rstrip() + "…"


def normalize_generated_title(raw: str, *, max_len: int = _TITLE_MAX_LEN) -> Optional[str]:
    """Normalize model output to a single-line title."""

    text = (raw or "").strip()
    text = text.strip('"\'「」『』【】《》')
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def _should_abort_title_after_metadata_refresh(metadata: Dict[str, Any]) -> bool:
    """Return True if user or session state forbids applying an auto-generated title."""

    md = metadata or {}
    if md.get("session_title_source") == "user":
        return True
    status = str(md.get("session_title_auto_status") or "")
    return status in ("skipped", "complete")


def run_chat_session_title_job_from_env(
    *,
    tenant_id: str,
    session_id: str,
    user_excerpt: str,
    assistant_excerpt: str,
) -> None:
    """Entrypoint for deferred workers (thread or Celery): reload settings from env and update SQLite."""

    settings = load_settings()
    run_chat_session_title_job(
        settings=settings,
        tenant_id=tenant_id,
        session_id=session_id,
        user_excerpt=user_excerpt,
        assistant_excerpt=assistant_excerpt,
    )


def run_chat_session_title_job(
    *,
    settings: AppSettings,
    tenant_id: str,
    session_id: str,
    user_excerpt: str,
    assistant_excerpt: str,
) -> None:
    """Ask the chat model for a short session title; skip when user locked title or job already done."""

    tid = (tenant_id or "").strip()
    sid = (session_id or "").strip()
    if not tid or not sid:
        return

    store = SqliteChatSessionStore(settings.sqlite_db_path)
    try:
        rec = store.get_session(tenant_id=tid, session_id=sid)
    except KeyError:
        _LOGGER.info("chat_session_title_skip_missing_session", tenant_id=tid, session_id=sid)
        return

    md = dict(rec.metadata or {})
    if md.get("session_title_source") == "user":
        _LOGGER.debug("chat_session_title_skip_user_locked", session_id=sid)
        return
    status = str(md.get("session_title_auto_status") or "")
    if status in ("complete", "skipped"):
        return

    client: Optional[DeepSeekChatCompletionClient] = _deepseek_client(settings)
    if client is None:
        try:
            fresh = store.get_session(tenant_id=tid, session_id=sid)
        except KeyError:
            return
        if _should_abort_title_after_metadata_refresh(dict(fresh.metadata or {})):
            return
        store.merge_session_metadata(
            tenant_id=tid,
            session_id=sid,
            patch={
                "session_title_auto_status": "failed",
                "session_title_auto_error": "deepseek_not_configured",
            },
        )
        _LOGGER.info("chat_session_title_skip_no_deepseek", session_id=sid)
        return

    u_ex = clip_excerpt(user_excerpt)
    a_ex = clip_excerpt(assistant_excerpt)
    system_msg = (
        "You name chat sessions for a product UI. Output exactly one short title in the same "
        "language as the user message when possible. No quotes, no numbering, no trailing punctuation noise."
    )
    user_msg = f"User:\n{u_ex}\n\nAssistant:\n{a_ex}\n\nReply with the title only."
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]
    req_id = f"title-{uuid.uuid4().hex[:12]}"
    trace_id = req_id
    try:
        result = client.complete_chat(
            messages,
            trace_id=trace_id,
            request_id=req_id,
            thinking=None,
            model_override=settings.chat_completion_model,
        )
        title = normalize_generated_title(result.text or "")
        if title is None:
            raise ValueError("empty_title")
        try:
            fresh = store.get_session(tenant_id=tid, session_id=sid)
        except KeyError:
            _LOGGER.info("chat_session_title_skip_missing_after_llm", session_id=sid)
            return
        md_fresh = dict(fresh.metadata or {})
        if _should_abort_title_after_metadata_refresh(md_fresh):
            _LOGGER.info(
                "chat_session_title_skip_conflict_after_llm",
                session_id=sid,
                session_title_auto_status=str(md_fresh.get("session_title_auto_status") or ""),
                session_title_source=str(md_fresh.get("session_title_source") or ""),
            )
            return
        md_fresh["session_title_auto_status"] = "complete"
        md_fresh.pop("session_title_auto_error", None)
        store.update_session(
            tenant_id=tid,
            session_id=sid,
            title=title,
            skill=fresh.skill,
            metadata=md_fresh,
        )
        _LOGGER.info(
            "chat_session_title_applied",
            tenant_id=tid,
            session_id=sid,
            title_len=len(title),
        )
    except Exception as exc:
        try:
            fresh = store.get_session(tenant_id=tid, session_id=sid)
        except KeyError:
            return
        if _should_abort_title_after_metadata_refresh(dict(fresh.metadata or {})):
            return
        store.merge_session_metadata(
            tenant_id=tid,
            session_id=sid,
            patch={
                "session_title_auto_status": "failed",
                "session_title_auto_error": str(exc)[:500],
            },
        )
        _LOGGER.warning(
            "chat_session_title_failed",
            tenant_id=tid,
            session_id=sid,
            error=str(exc),
        )


__all__ = [
    "clip_excerpt",
    "normalize_generated_title",
    "run_chat_session_title_job",
    "run_chat_session_title_job_from_env",
]
