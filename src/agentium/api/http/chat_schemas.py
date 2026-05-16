"""Pydantic payloads for TradeAgent-aligned ``/v1/chat/*`` HTTP handlers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import Literal

from agentium.api.http.control_plane_schemas import MessageDisposition, McpExecutionTier

OrchestrationMode = Literal["workflow", "agentic", "research"]


class WorkspaceAgentConfig(BaseModel):
    """Workbench Agent builder payload stored under session ``metadata.workspace_agent``."""

    schema_version: int = Field(default=1, ge=1, le=256)
    skill_tags: List[str] = Field(default_factory=list, max_length=16)
    chat_tool_allowlist: List[str] = Field(default_factory=list, max_length=32)
    persona_identity_md: Optional[str] = None
    persona_soul_md: Optional[str] = None
    persona_tools_md: Optional[str] = None
    persona_user_md: Optional[str] = None
    memory_plugin: Literal["native", "mem0"] = Field(
        default="native",
        description="Chat memory lane: built-in layered store vs Mem0 platform (requires server lanes).",
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("skill_tags", mode="after")
    @classmethod
    def _normalize_skill_tags(cls, tags: List[str]) -> List[str]:
        seen: set[str] = set()
        out: List[str] = []
        for raw in tags:
            tag = (raw or "").strip()
            if not tag:
                continue
            if tag in seen:
                continue
            seen.add(tag)
            out.append(tag)
        return out

    @field_validator("chat_tool_allowlist", mode="after")
    @classmethod
    def _normalize_tools(cls, names: List[str]) -> List[str]:
        seen: set[str] = set()
        out: List[str] = []
        for raw in names:
            name = (raw or "").strip()
            if not name:
                continue
            if name in seen:
                continue
            seen.add(name)
            out.append(name)
        return out


class ChatSessionCreateRequest(BaseModel):
    """Create chat session payload."""

    session_id: Optional[str] = Field(default=None, max_length=128)
    title: Optional[str] = None
    skill: Optional[str] = None
    intro_text: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    workspace_agent: Optional[WorkspaceAgentConfig] = None
    orchestration_mode: OrchestrationMode = Field(
        default="agentic",
        description="Workflow vs agentic vs research triage; persisted under session metadata.",
    )
    policy_pack_id: Optional[str] = Field(
        default=None,
        max_length=256,
        description="Optional governance policy pack id recorded in session metadata.",
    )

    model_config = ConfigDict(extra="forbid")


class ChatSessionUpdateRequest(BaseModel):
    """Update mutable session fields (title and/or ``skill`` tag)."""

    title: Optional[str] = Field(default=None, min_length=1)
    skill: Optional[str] = Field(default=None, max_length=128)
    workspace_agent: Optional[WorkspaceAgentConfig] = None
    orchestration_mode: Optional[OrchestrationMode] = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def at_least_one_field(self) -> ChatSessionUpdateRequest:
        if (
            self.title is None
            and self.skill is None
            and self.workspace_agent is None
            and self.orchestration_mode is None
        ):
            raise ValueError(
                "At least one of title, skill, workspace_agent, or orchestration_mode is required."
            )
        return self


class ChatMessageSendRequest(BaseModel):
    """Send one user message within a chat session."""

    session_id: str = Field(min_length=1)
    content: str = Field(default="", description="User-visible body; may be empty when regenerating.")
    message_disposition: MessageDisposition = Field(default="collect")
    mcp_execution_tier: McpExecutionTier = Field(
        default="direct-tool",
        description="Ingress tier for audit/observability (same semantics as POST /v1/turn).",
    )
    auto_ingress: bool = Field(
        default=False,
        description="When true, server derives disposition and MCP tier from message content.",
    )
    regenerate_from_message_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="When set, drops the latest assistant row for this message id and re-runs the LLM "
        "without appending a duplicate user row.",
    )
    agent_skill: Optional[str] = None
    llm_model: Optional[str] = None
    stream: bool = False
    enable_agent_tools: bool = Field(
        default=False,
        description="When true and server enables AGENTIUM_CHAT_AGENT_TOOLS, model may call low-risk tools.",
    )
    deepseek_thinking_enabled: Optional[bool] = Field(
        default=None,
        description="When set, overrides AGENTIUM_DEEPSEEK_THINKING_ENABLED for this message.",
    )
    deepseek_reasoning_effort: Optional[str] = Field(
        default=None,
        max_length=32,
        description="Overrides reasoning-effort hint for DeepSeek thinking mode (normalized to high|max).",
    )
    orchestration_mode: Optional[OrchestrationMode] = Field(
        default=None,
        description="When set, patches session metadata before this turn runs.",
    )

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def content_required_unless_regenerate(self) -> ChatMessageSendRequest:
        regen = (self.regenerate_from_message_id or "").strip()
        if regen:
            return self
        if not (self.content or "").strip():
            raise ValueError("content is required unless regenerate_from_message_id is set.")
        return self
