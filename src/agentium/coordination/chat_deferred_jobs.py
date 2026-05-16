"""Backward-compatible exports for chat deferred execution.

Prefer importing :mod:`agentium.coordination.deferred_tasks` directly — handlers are
plugin-registered by kind (see :func:`agentium.coordination.deferred_tasks.register_deferred_handler`).
"""

from __future__ import annotations

from agentium.coordination.deferred_tasks import (
    DeferredTaskSink as ChatDeferredJobSink,
    build_deferred_task_sink,
)

build_chat_deferred_job_sink = build_deferred_task_sink

__all__ = [
    "ChatDeferredJobSink",
    "build_chat_deferred_job_sink",
]
