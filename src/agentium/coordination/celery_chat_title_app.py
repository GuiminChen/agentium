"""Backward-compatible Celery module path.

Run worker (recommended module)::

    celery -A agentium.coordination.deferred_tasks.celery_backend worker -l info

This shim keeps ``celery -A agentium.coordination.celery_chat_title_app`` working.
"""

from __future__ import annotations

from agentium.coordination.deferred_tasks.celery_backend import (
    celery_app,
    chat_generate_session_title_task,
    configure_celery_broker,
    deferred_dispatch_task,
    ensure_broker_from_env,
)

__all__ = [
    "celery_app",
    "chat_generate_session_title_task",
    "configure_celery_broker",
    "deferred_dispatch_task",
    "ensure_broker_from_env",
]
