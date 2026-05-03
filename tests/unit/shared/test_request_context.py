from __future__ import annotations

from contextvars import Context

import pytest

from agentium.models.context import RequestContext
from agentium.shared.errors import ConfigurationError
from agentium.shared.request_context import get_request_context, set_request_context


def test_request_context_set_and_get() -> None:
    context = RequestContext(
        request_id="req-2",
        run_id="run-2",
        tenant_id="tenant-x",
        user_id="user-2",
        trace_id="trace-2",
        role="analyst",
        deployment_mode="prod",
    )
    set_request_context(context)

    actual = get_request_context()

    assert actual == context


def test_get_request_context_raises_when_not_set() -> None:
    fresh_context = Context()
    with pytest.raises(ConfigurationError):
        fresh_context.run(get_request_context)
