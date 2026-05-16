"""Celery-backed deferred enqueue (Redis broker)."""

from __future__ import annotations

import json
from typing import Any, Mapping

import structlog

_LOGGER = structlog.get_logger(__name__)


class CeleryDeferredTaskSink:
    """Push JSON payloads to ``agentium.deferred.dispatch``."""

    def __init__(self, *, redis_url: str) -> None:
        self._redis_url = redis_url.strip()

    def enqueue(self, kind: str, payload: Mapping[str, Any], *, lane: str = "default") -> None:
        from agentium.coordination.deferred_tasks.celery_backend import (
            configure_celery_broker,
            deferred_dispatch_task,
        )

        kind_clean = (kind or "").strip()
        if not kind_clean:
            _LOGGER.warning("deferred_celery_skip_empty_kind")
            return

        configure_celery_broker(self._redis_url)
        try:
            blob = json.dumps(dict(payload), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            _LOGGER.warning("deferred_celery_payload_json_failed", kind=kind_clean, error=str(exc))
            return

        lane_clean = (lane or "default").strip() or "default"
        deferred_dispatch_task.delay(kind_clean, blob, lane_clean)


__all__ = ["CeleryDeferredTaskSink"]
