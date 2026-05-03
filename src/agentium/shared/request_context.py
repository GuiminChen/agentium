"""Request context helpers for concurrent runtime isolation."""

from __future__ import annotations

from contextvars import ContextVar

from agentium.models.context import RequestContext
from agentium.shared.errors import ConfigurationError

_REQUEST_CONTEXT: ContextVar[RequestContext | None] = ContextVar(
    "agentium_request_context", default=None
)


def set_request_context(context: RequestContext) -> None:
    """Set request context for current execution scope.

    Args:
        context: Strongly typed request context object.
    """

    _REQUEST_CONTEXT.set(context)


def get_request_context() -> RequestContext:
    """Get request context from current execution scope.

    Returns:
        RequestContext: Context object bound to current scope.

    Raises:
        ConfigurationError: If no context was previously configured.
    """

    context = _REQUEST_CONTEXT.get()
    if context is None:
        raise ConfigurationError("RequestContext is not set for current execution scope")
    return context
