"""Built-in deferred handlers (register on import).

Extensions can call :func:`~agentium.coordination.deferred_tasks.registry.register_deferred_handler`
from their own modules; ensure those modules are imported on API and worker processes.
"""

from __future__ import annotations

from typing import Any, Mapping

from agentium.coordination.deferred_tasks.kinds import KIND_CHAT_GENERATE_SESSION_TITLE
from agentium.coordination.deferred_tasks.registry import register_deferred_handler


def _handle_chat_generate_session_title(payload: Mapping[str, Any]) -> None:
    from agentium.coordination.chat_session_title_job import run_chat_session_title_job_from_env

    run_chat_session_title_job_from_env(
        tenant_id=str(payload.get("tenant_id") or ""),
        session_id=str(payload.get("session_id") or ""),
        user_excerpt=str(payload.get("user_excerpt") or ""),
        assistant_excerpt=str(payload.get("assistant_excerpt") or ""),
    )


register_deferred_handler(KIND_CHAT_GENERATE_SESSION_TITLE, _handle_chat_generate_session_title)
