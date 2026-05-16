"""Plugin-style deferred task execution (no Redis → thread pool; Redis → Celery).

Public API
----------

- :class:`~agentium.coordination.deferred_tasks.sink.DeferredTaskSink`
- :func:`~agentium.coordination.deferred_tasks.registry.register_deferred_handler`
- :func:`~agentium.coordination.deferred_tasks.factory.build_deferred_task_sink`
"""

from __future__ import annotations

from agentium.coordination.deferred_tasks.factory import build_deferred_task_sink
from agentium.coordination.deferred_tasks.kinds import (
    DEFAULT_DEFERRED_LANE,
    KIND_CHAT_GENERATE_SESSION_TITLE,
    LANE_CHAT,
)
from agentium.coordination.deferred_tasks.registry import (
    register_deferred_handler,
    registered_deferred_kinds,
    run_deferred_handler,
)
from agentium.coordination.deferred_tasks.sink import DeferredTaskSink

__all__ = [
    "DEFAULT_DEFERRED_LANE",
    "DeferredTaskSink",
    "KIND_CHAT_GENERATE_SESSION_TITLE",
    "LANE_CHAT",
    "build_deferred_task_sink",
    "register_deferred_handler",
    "registered_deferred_kinds",
    "run_deferred_handler",
]
