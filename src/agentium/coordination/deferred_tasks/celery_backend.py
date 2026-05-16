"""Celery application and dispatch task for plugin deferred handlers.

Worker::

    celery -A agentium.coordination.deferred_tasks.celery_backend worker -l info

Legacy module ``celery_chat_title_app`` re-exports this app for existing docs/commands.

Requires ``AGENTIUM_REDIS_URL`` (or programmatic :func:`configure_celery_broker`).
"""

from __future__ import annotations

import json
import os
from typing import Optional

from celery import Celery

import structlog

_LOGGER = structlog.get_logger(__name__)

celery_app = Celery("agentium_deferred")
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


def configure_celery_broker(redis_url: str) -> None:
    """Point broker + result backend at Redis (idempotent)."""

    url = redis_url.strip()
    celery_app.conf.broker_url = url
    celery_app.conf.result_backend = url


def ensure_broker_from_env() -> Optional[str]:
    """If ``AGENTIUM_REDIS_URL`` is set, configure broker."""

    url = os.getenv("AGENTIUM_REDIS_URL", "").strip()
    if url:
        configure_celery_broker(url)
        return url
    return None


@celery_app.task(name="agentium.deferred.dispatch")
def deferred_dispatch_task(kind: str, payload_json: str, lane: str = "default") -> None:
    """Dispatch by kind via the shared registry."""

    import agentium.coordination.deferred_tasks.builtins  # noqa: F401 — register handlers

    from agentium.coordination.deferred_tasks.registry import run_deferred_handler

    kind_clean = (kind or "").strip()
    try:
        raw = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        _LOGGER.warning("deferred_celery_bad_json", kind=kind_clean, error=str(exc))
        return
    if not isinstance(raw, dict):
        _LOGGER.warning("deferred_celery_payload_not_object", kind=kind_clean)
        return
    _LOGGER.debug(
        "deferred_celery_dispatch",
        kind=kind_clean,
        lane=(lane or "default").strip() or "default",
    )
    try:
        run_deferred_handler(kind_clean, raw)
    except Exception as exc:
        _LOGGER.warning(
            "deferred_celery_handler_failed",
            kind=kind_clean,
            error=str(exc),
        )


@celery_app.task(name="agentium.chat.generate_session_title")
def chat_generate_session_title_task(
    tenant_id: str,
    session_id: str,
    user_excerpt: str,
    assistant_excerpt: str,
) -> None:
    """Backward-compatible task name; forwards through registry."""

    import agentium.coordination.deferred_tasks.builtins  # noqa: F401

    from agentium.coordination.deferred_tasks.kinds import KIND_CHAT_GENERATE_SESSION_TITLE
    from agentium.coordination.deferred_tasks.registry import run_deferred_handler

    run_deferred_handler(
        KIND_CHAT_GENERATE_SESSION_TITLE,
        {
            "tenant_id": tenant_id,
            "session_id": session_id,
            "user_excerpt": user_excerpt,
            "assistant_excerpt": assistant_excerpt,
        },
    )


# Warm broker from env when module loads (worker / one-off scripts).
ensure_broker_from_env()

__all__ = [
    "celery_app",
    "chat_generate_session_title_task",
    "configure_celery_broker",
    "deferred_dispatch_task",
    "ensure_broker_from_env",
]
