"""Protocol for enqueueing deferred work (thread pool or Celery)."""

from __future__ import annotations

from typing import Any, Mapping, Protocol


class DeferredTaskSink(Protocol):
    """Enqueue durable-ish background work outside the HTTP/request thread.

    Implementations:
        - :class:`~agentium.coordination.deferred_tasks.thread_sink.ThreadDeferredTaskSink`
          — in-process bounded pool (optional ``lane`` metadata for logging / future per-lane caps).
        - :class:`~agentium.coordination.deferred_tasks.celery_sink.CeleryDeferredTaskSink`
          — Celery + Redis broker.
    """

    def enqueue(self, kind: str, payload: Mapping[str, Any], *, lane: str = "default") -> None:
        ...


__all__ = ["DeferredTaskSink"]
