"""Chat session update request validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentium.api.http.chat_schemas import ChatSessionUpdateRequest


def test_chat_session_update_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError):
        ChatSessionUpdateRequest.model_validate({})


def test_chat_session_update_accepts_title_only() -> None:
    row = ChatSessionUpdateRequest.model_validate({"title": "Hello"})
    assert row.title == "Hello"
    assert row.skill is None


def test_chat_session_update_accepts_skill_only() -> None:
    row = ChatSessionUpdateRequest.model_validate({"skill": "my_skill_tag"})
    assert row.skill == "my_skill_tag"
    assert row.title is None


def test_chat_session_update_accepts_workspace_agent_only() -> None:
    row = ChatSessionUpdateRequest.model_validate(
        {"workspace_agent": {"skill_tags": ["workspace_agent"], "schema_version": 1}},
    )
    assert row.workspace_agent is not None
    assert row.workspace_agent.skill_tags == ["workspace_agent"]


def test_chat_session_update_accepts_both() -> None:
    row = ChatSessionUpdateRequest.model_validate({"title": "T", "skill": "s"})
    assert row.title == "T"
    assert row.skill == "s"
