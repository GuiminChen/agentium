"""In-process deferred execution: bounded thread pool + FIFO dispatch via executor queue.

Optional per-task ``lane`` values can tag logical queues; execution uses one shared pool
with configurable worker count (no cross-process durability). Suitable when Redis is absent.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Mapping

import structlog

from agentium.app.settings import AppSettings

_LOGGER = structlog.get_logger(__name__)


class ThreadDeferredTaskSink:
    """Submit handlers to a lazily created :class:`~concurrent.futures.ThreadPoolExecutor`."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        max_workers = max(1, min(32, int(settings.deferred_thread_pool_size)))
        self._max_workers = max_workers
        self._executor: ThreadPoolExecutor | None = None
        self._executor_lock = threading.Lock()

    def _pool(self) -> ThreadPoolExecutor:
        with self._executor_lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self._max_workers,
                    thread_name_prefix="agentium-deferred",
                )
                _LOGGER.info(
                    "deferred_thread_pool_started",
                    max_workers=self._max_workers,
                    data_dir=str(self._settings.data_dir),
                )
            return self._executor

    def enqueue(self, kind: str, payload: Mapping[str, Any], *, lane: str = "default") -> None:
        """Schedule ``run_deferred_handler(kind, payload)`` on the pool."""

        kind_clean = (kind or "").strip()
        lane_clean = (lane or "default").strip() or "default"

        def _run() -> None:
            from agentium.coordination.deferred_tasks.registry import run_deferred_handler

            try:
                run_deferred_handler(kind_clean, payload)
            except Exception as exc:
                _LOGGER.warning(
                    "deferred_task_thread_failed",
                    kind=kind_clean,
                    lane=lane_clean,
                    error=str(exc),
                )

        try:
            self._pool().submit(_run)
        except RuntimeError as exc:
            _LOGGER.warning(
                "deferred_task_thread_submit_failed",
                kind=kind_clean,
                lane=lane_clean,
                error=str(exc),
            )


__all__ = ["ThreadDeferredTaskSink"]
