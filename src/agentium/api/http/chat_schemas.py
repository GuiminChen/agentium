"""Pydantic payloads for TradeAgent-aligned ``/v1/chat/*`` HTTP handlers."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from agentium.api.http.control_plane_schemas import MessageDisposition, McpExecutionTier


class ChatSessionCreateRequest(BaseModel):
    """Create chat session payload."""

    session_id: Optional[str] = Field(default=None, max_length=128)
    title: Optional[str] = None
    skill: Optional[str] = None
    intro_text: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"


class ChatSessionUpdateRequest(BaseModel):
    """Update mutable session fields (currently title only)."""

    title: str = Field(min_length=1)

    class Config:
        extra = "forbid"


class ChatMessageSendRequest(BaseModel):
    """Send one user message within a chat session."""

    session_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    message_disposition: MessageDisposition = Field(default="collect")
    mcp_execution_tier: McpExecutionTier = Field(
        default="direct-tool",
        description="Ingress tier for audit/observability (same semantics as POST /v1/turn).",
    )
    agent_skill: Optional[str] = None
    llm_model: Optional[str] = None
    stream: bool = False

    class Config:
        extra = "forbid"
