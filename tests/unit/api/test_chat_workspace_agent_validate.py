"""Tests for workspace_agent validation helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.api.http.chat_schemas import WorkspaceAgentConfig
from agentium.api.http.chat_workspace_agent import serialize_workspace_agent_for_storage
from tests.unit.skills.test_skill_catalog import _settings_for_catalog


def test_serialize_rejects_unknown_skill_tag(tmp_path: Path) -> None:
    settings = _settings_for_catalog(tmp_path, project=None, user=None, config=None)
    agent = WorkspaceAgentConfig(skill_tags=["definitely-not-a-real-pack-id"])
    with pytest.raises(ValueError, match="Unknown or disallowed"):
        serialize_workspace_agent_for_storage(
            agent,
            settings=settings,
            tool_registry=None,
            allowed_skill_tags={"workspace_agent"},
        )


def test_serialize_rejects_oversized_persona(tmp_path: Path) -> None:
    settings = _settings_for_catalog(tmp_path, project=None, user=None, config=None)
    agent = WorkspaceAgentConfig(
        skill_tags=["workspace_agent"],
        persona_identity_md="x" * (settings.workspace_agent_persona_max_chars + 1),
    )
    with pytest.raises(ValueError, match="persona_identity_md exceeds"):
        serialize_workspace_agent_for_storage(
            agent,
            settings=settings,
            tool_registry=None,
            allowed_skill_tags={"workspace_agent"},
        )


def test_serialize_rejects_oversized_persona_tools(tmp_path: Path) -> None:
    settings = _settings_for_catalog(tmp_path, project=None, user=None, config=None)
    agent = WorkspaceAgentConfig(
        skill_tags=["workspace_agent"],
        persona_tools_md="x" * (settings.workspace_agent_persona_max_chars + 1),
    )
    with pytest.raises(ValueError, match="persona_tools_md exceeds"):
        serialize_workspace_agent_for_storage(
            agent,
            settings=settings,
            tool_registry=None,
            allowed_skill_tags={"workspace_agent"},
        )


def test_serialize_accepts_workspace_agent_minimal(tmp_path: Path) -> None:
    settings = _settings_for_catalog(tmp_path, project=None, user=None, config=None)
    agent = WorkspaceAgentConfig(skill_tags=["workspace_agent"])
    blob = serialize_workspace_agent_for_storage(
        agent,
        settings=settings,
        tool_registry=None,
        allowed_skill_tags={"workspace_agent"},
    )
    assert blob["skill_tags"] == ["workspace_agent"]
    assert blob["chat_tool_allowlist"] == []
    assert blob["memory_plugin"] == "native"


def test_serialize_preserves_memory_plugin_mem0(tmp_path: Path) -> None:
    settings = _settings_for_catalog(tmp_path, project=None, user=None, config=None)
    agent = WorkspaceAgentConfig(skill_tags=["workspace_agent"], memory_plugin="mem0")
    blob = serialize_workspace_agent_for_storage(
        agent,
        settings=settings,
        tool_registry=None,
        allowed_skill_tags={"workspace_agent"},
    )
    assert blob["memory_plugin"] == "mem0"
