"""Validate ``workspace_agent`` payloads embedded in chat session ``metadata``."""

from __future__ import annotations

import re
from typing import AbstractSet, Any, Dict, Optional

from agentium.api.http.chat_schemas import WorkspaceAgentConfig
from agentium.app.settings import AppSettings
from agentium.tools.tool_registry import ToolRegistry

_SKILL_TAG_SAFE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
_TOOL_NAME_SAFE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def serialize_workspace_agent_for_storage(
    agent: WorkspaceAgentConfig,
    *,
    settings: AppSettings,
    tool_registry: Optional[ToolRegistry],
    allowed_skill_tags: AbstractSet[str],
) -> Dict[str, Any]:
    """Normalize and validate workspace agent config for JSON metadata persistence.

    Args:
        agent: Parsed client payload.
        settings: Size limits for persona markdown sections.
        tool_registry: Used to validate chat tool allowlist names (may be ``None``).
        allowed_skill_tags: Skill ids allowed for this tenant (builtin + merged manifests).

    Returns:
        JSON-serializable dict suitable for ``metadata[\"workspace_agent\"]``.

    Raises:
        ValueError: Invalid tags, tools, or oversized persona sections.
    """

    max_skills = max(1, settings.workspace_agent_max_skill_tags)
    tags = list(agent.skill_tags)[:max_skills]
    for tag in tags:
        if not _SKILL_TAG_SAFE.match(tag.strip()):
            raise ValueError(f"Invalid skill tag: {tag!r}")
        stem = tag.strip()
        if stem not in allowed_skill_tags:
            raise ValueError(f"Unknown or disallowed skill tag: {stem!r}")

    max_tools = max(1, settings.workspace_agent_max_tool_allowlist)
    allow_tools = list(agent.chat_tool_allowlist)[:max_tools]
    chat_eligible: AbstractSet[str]
    if tool_registry is not None:
        chat_eligible = {s.name for s in tool_registry.list_chat_agent_tool_specs()}
    else:
        chat_eligible = frozenset()

    if allow_tools:
        if tool_registry is None or not chat_eligible:
            raise ValueError("chat_tool_allowlist requires configured tool registry with chat-eligible tools.")
        for name in allow_tools:
            if not _TOOL_NAME_SAFE.match(name.strip()):
                raise ValueError(f"Invalid tool name: {name!r}")
            stem = name.strip()
            if stem not in chat_eligible:
                raise ValueError(f"Tool not allowed for chat agent loop: {stem!r}")

    lim = max(0, settings.workspace_agent_persona_max_chars)
    persona_identity_md = _trim_persona(agent.persona_identity_md, lim, "persona_identity_md")
    persona_soul_md = _trim_persona(agent.persona_soul_md, lim, "persona_soul_md")
    persona_tools_md = _trim_persona(agent.persona_tools_md, lim, "persona_tools_md")
    persona_user_md = _trim_persona(agent.persona_user_md, lim, "persona_user_md")

    normalized = WorkspaceAgentConfig(
        schema_version=agent.schema_version,
        skill_tags=tags,
        chat_tool_allowlist=allow_tools,
        persona_identity_md=persona_identity_md,
        persona_soul_md=persona_soul_md,
        persona_tools_md=persona_tools_md,
        persona_user_md=persona_user_md,
        memory_plugin=agent.memory_plugin,
    )
    return normalized.model_dump(mode="json", exclude_none=True)


def _trim_persona(raw: Optional[str], limit: int, field: str) -> Optional[str]:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if limit <= 0:
        raise ValueError(f"{field} exceeds maximum length (policy disables persona text).")
    if len(text) > limit:
        raise ValueError(f"{field} exceeds maximum length ({limit} characters).")
    return text


def allowed_skill_tag_set(settings: AppSettings) -> set[str]:
    """Return skill ids eligible for workspace_agent.skill_tags (mirrors skill-options endpoint)."""

    from agentium.skills.catalog import load_merged_skill_manifests

    tags: set[str] = {"workspace_agent"}
    try:
        for manifest in load_merged_skill_manifests(settings):
            tags.add(manifest.name)
    except (OSError, RuntimeError, ValueError):
        pass
    return tags


def parse_workspace_agent_blob(metadata: Dict[str, Any]) -> Optional[WorkspaceAgentConfig]:
    """Return validated config from session metadata or ``None`` if absent/invalid."""

    raw = metadata.get("workspace_agent")
    if raw is None or raw == {}:
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return WorkspaceAgentConfig.model_validate(raw)
    except Exception:
        return None
