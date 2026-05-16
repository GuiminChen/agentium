"""Plugin registry for deferred task handlers (kind → callable).

Handlers are registered at import time (builtins) or by extensions calling
:func:`register_deferred_handler`. Celery workers must import the same builtins
module so kinds resolve consistently.
"""

from __future__ import annotations

from threading import Lock
from typing import Any, Callable, Dict, Mapping

import structlog

_LOGGER = structlog.get_logger(__name__)

DeferredHandler = Callable[[Mapping[str, Any]], None]

_LOCK = Lock()
_HANDLERS: Dict[str, DeferredHandler] = {}


def register_deferred_handler(kind: str, handler: DeferredHandler) -> None:
    """Register or replace the handler for ``kind`` (must be stable across processes)."""

    key = (kind or "").strip()
    if not key:
        raise ValueError("deferred kind must be non-empty")
    with _LOCK:
        _HANDLERS[key] = handler


def get_deferred_handler(kind: str) -> DeferredHandler | None:
    """Return handler for ``kind`` or ``None``."""

    with _LOCK:
        return _HANDLERS.get((kind or "").strip())


def registered_deferred_kinds() -> tuple[str, ...]:
    """Snapshot of registered kinds (observability / tests)."""

    with _LOCK:
        return tuple(sorted(_HANDLERS.keys()))


def run_deferred_handler(kind: str, payload: Mapping[str, Any]) -> None:
    """Invoke handler for ``kind``; logs and returns if unknown."""

    fn = get_deferred_handler(kind)
    if fn is None:
        _LOGGER.warning("deferred_handler_unknown_kind", kind=kind)
        return
    fn(payload)


def clear_deferred_handlers_for_tests() -> None:
    """Remove all handlers (unit tests only)."""

    with _LOCK:
        _HANDLERS.clear()


__all__ = [
    "DeferredHandler",
    "clear_deferred_handlers_for_tests",
    "get_deferred_handler",
    "register_deferred_handler",
    "registered_deferred_kinds",
    "run_deferred_handler",
]
