"""Core runtime models for request and governance flow."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


MessageDisposition = Literal["collect", "followup", "steer"]
McpExecutionTier = Literal["direct-tool", "code-exec-mcp"]


class DecisionType(str, Enum):
    """Supported governance decisions for tool execution."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class RequestContext(BaseModel):
    """Runtime request context propagated across system layers.

    Attributes:
        request_id: Unique id for the inbound request.
        run_id: Stable id for this runtime execution.
        tenant_id: Tenant isolation key.
        user_id: Caller identity key.
        trace_id: Distributed trace correlation id.
        role: Caller role used for policy matching.
        deployment_mode: Runtime mode such as prod/dev.
        run_manifest_sha256: Optional digest of validated RunManifest for audit correlation.
        manifest_declared_tools: When not None, tool name must be in this list for execution
            (from RunManifest.declared_tools on ingress). None skips manifest allowlist.
    """

    request_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    role: str = Field(default="user", min_length=1)
    deployment_mode: str = Field(default="prod", min_length=1)
    run_manifest_sha256: Optional[str] = Field(
        default=None,
        description="SHA-256 of canonical run manifest when supplied on ingress.",
    )
    manifest_declared_tools: Optional[List[str]] = Field(
        default=None,
        description="If set, only these tool names may execute (run manifest allowlist).",
    )
    message_disposition: MessageDisposition = Field(
        default="collect",
        description="Channel/session semantics: collect, followup, or steer (PRD §3.5.1).",
    )
    mcp_execution_tier: McpExecutionTier = Field(
        default="direct-tool",
        description="MCP path grade: direct registry tool vs code-exec MCP (PRD §3.9.2).",
    )

    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolCallRecord(BaseModel):
    """Tool invocation record for runtime observability and audit.

    Attributes:
        tool_name: Registered tool name.
        tool_use_id: Unique id for one tool execution.
        args_hash: Stable hash fingerprint for tool args.
        status: Result status such as success/denied/failed.
        latency_ms: End-to-end tool latency in milliseconds.
    """

    tool_name: str = Field(min_length=1)
    tool_use_id: str = Field(min_length=1)
    args_hash: str = Field(min_length=1)
    status: str = Field(min_length=1)
    latency_ms: int = Field(ge=0)

    class Config:
        """Pydantic model configuration."""

        extra = "forbid"


class Decision(BaseModel):
    """Policy decision for a candidate tool call.

    Attributes:
        decision: Allow, deny, or require approval.
        reason: Human-readable reason for decision.
        rule_id: Optional matched rule identifier.
    """

    decision: DecisionType
    reason: str = Field(min_length=1)
    rule_id: Optional[str] = None

    class Config:
        """Pydantic model configuration."""

        extra = "forbid"


class AuditRecord(BaseModel):
    """Immutable audit event payload for key runtime actions.

    Attributes:
        event_type: Audit event category.
        timestamp: UTC time when event happened.
        tenant_id: Tenant owning this event.
        run_id: Runtime id owning this event.
        policy_version: Optional policy version used for event.
        payload: Event-specific structured payload.
    """

    event_type: str = Field(min_length=1)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tenant_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    policy_version: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        """Pydantic model configuration."""

        extra = "forbid"
