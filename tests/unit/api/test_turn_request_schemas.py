"""Turn / resume HTTP body validation for ingress extensions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentium.api.http.control_plane_schemas import ResumeTurnRequest, TurnRequest


def test_turn_request_defaults_disposition_and_tier() -> None:
    body = {
        "tool_name": "echo_tool",
        "args": {},
        "run_id": "r1",
        "request_id": "q1",
        "trace_id": "t1",
    }
    req = TurnRequest.model_validate(body)
    assert req.message_disposition == "collect"
    assert req.mcp_execution_tier == "direct-tool"


def test_turn_request_accepts_explicit_disposition_and_tier() -> None:
    body = {
        "tool_name": "echo_tool",
        "run_id": "r1",
        "request_id": "q1",
        "trace_id": "t1",
        "message_disposition": "steer",
        "mcp_execution_tier": "code-exec-mcp",
    }
    req = TurnRequest.model_validate(body)
    assert req.message_disposition == "steer"
    assert req.mcp_execution_tier == "code-exec-mcp"


def test_turn_request_rejects_invalid_disposition() -> None:
    body = {
        "tool_name": "echo_tool",
        "run_id": "r1",
        "request_id": "q1",
        "trace_id": "t1",
        "message_disposition": "nope",
    }
    with pytest.raises(ValidationError):
        TurnRequest.model_validate(body)


def test_resume_request_inherits_same_fields() -> None:
    body = {
        "tool_name": "echo_tool",
        "run_id": "r1",
        "request_id": "q1",
        "trace_id": "t1",
        "approval_id": "a1",
        "message_disposition": "followup",
    }
    req = ResumeTurnRequest.model_validate(body)
    assert req.message_disposition == "followup"
    assert req.mcp_execution_tier == "direct-tool"
