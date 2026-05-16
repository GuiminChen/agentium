"""Select deferred task backend (thread pool vs Celery/Redis)."""

from __future__ import annotations

from typing import Optional

import structlog

from agentium.app.settings import AppSettings
from agentium.coordination.deferred_tasks.celery_sink import CeleryDeferredTaskSink
from agentium.coordination.deferred_tasks.sink import DeferredTaskSink
from agentium.coordination.deferred_tasks.thread_sink import ThreadDeferredTaskSink

_LOGGER = structlog.get_logger(__name__)


def build_deferred_task_sink(settings: AppSettings) -> Optional[DeferredTaskSink]:
    """Return a sink when deferred subsystem enabled; pick Celery when configured.

    Imports builtins so built-in kinds are registered before first enqueue.
    """

    import agentium.coordination.deferred_tasks.builtins  # noqa: F401

    if not settings.deferred_tasks_enabled:
        _LOGGER.info("deferred_tasks_disabled")
        return None

    want_celery = settings.deferred_task_backend == "celery"
    redis_url = (settings.redis_url or "").strip()

    if want_celery and redis_url:
        try:
            import celery  # noqa: F401
        except ImportError:
            _LOGGER.warning(
                "deferred_tasks_celery_unavailable_fallback_thread",
                reason="celery_not_installed",
            )
            return ThreadDeferredTaskSink(settings)

        from agentium.coordination.deferred_tasks.celery_backend import configure_celery_broker

        configure_celery_broker(redis_url)
        _LOGGER.info("deferred_tasks_backend_selected", backend="celery")
        return CeleryDeferredTaskSink(redis_url=redis_url)

    if want_celery and not redis_url:
        _LOGGER.warning("deferred_tasks_celery_missing_redis_fallback_thread")

    _LOGGER.info("deferred_tasks_backend_selected", backend="thread")
    return ThreadDeferredTaskSink(settings)


__all__ = ["build_deferred_task_sink"]
