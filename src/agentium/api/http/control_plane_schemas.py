"""Pydantic request bodies for the HTTP control plane."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from agentium.api.control_plane import ApprovalDecisionType
from agentium.governance.policy_release import PolicyBundle

MessageDisposition = Literal["collect", "followup", "steer"]
McpExecutionTier = Literal["direct-tool", "code-exec-mcp"]


class TurnRequest(BaseModel):
    """Request payload for running one tool turn."""

    tool_name: str = Field(min_length=1)
    args: Dict[str, Any] = Field(default_factory=dict)
    run_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    deployment_mode: str = Field(default="prod", min_length=1)
    run_manifest: Optional[Dict[str, Any]] = None
    message_disposition: MessageDisposition = Field(default="collect")
    mcp_execution_tier: McpExecutionTier = Field(default="direct-tool")

    class Config:
        extra = "forbid"


class ResumeTurnRequest(BaseModel):
    """Request payload for resuming one pending turn."""

    tool_name: str = Field(min_length=1)
    args: Dict[str, Any] = Field(default_factory=dict)
    run_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    deployment_mode: str = Field(default="prod", min_length=1)
    approval_id: str = Field(min_length=1)
    run_manifest: Optional[Dict[str, Any]] = None
    message_disposition: MessageDisposition = Field(default="collect")
    mcp_execution_tier: McpExecutionTier = Field(default="direct-tool")

    class Config:
        extra = "forbid"


class ApprovalDecisionRequest(BaseModel):
    """Request payload for approval decision endpoint."""

    decision: ApprovalDecisionType
    approver_id: str = Field(min_length=1)
    comment: str = ""

    class Config:
        extra = "forbid"


class PolicyReleaseSubmitRequest(BaseModel):
    """Request payload for submitting one signed policy release."""

    run_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    bundle: PolicyBundle

    class Config:
        extra = "forbid"


class PolicyReleaseApprovalRequest(BaseModel):
    """Request payload for approving one policy release."""

    approver_id: str = Field(min_length=1)
    comment: str = ""

    class Config:
        extra = "forbid"


class PolicyReleaseActivateRequest(BaseModel):
    """Request payload for canary-activating one policy release."""

    tenant_ids: List[str] = Field(min_items=1)
    activated_by: str = Field(min_length=1)

    class Config:
        extra = "forbid"


class PolicyReleaseRollbackRequest(BaseModel):
    """Request payload for rolling back one policy release."""

    rolled_back_by: str = Field(min_length=1)

    class Config:
        extra = "forbid"


class ResearchRunRequest(BaseModel):
    """Start DeepResearch workflow (GA HTTP)."""

    query: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    deployment_mode: str = Field(default="prod", min_length=1)
    vertical_template: str = Field(
        default="general",
        min_length=1,
        max_length=64,
        description="Vertical scenario id (e.g. fixed_income); stub pipeline echoes in report metadata.",
    )

    class Config:
        extra = "forbid"


class WorkflowResumeRequest(BaseModel):
    """Resume workflow after HITL approval."""

    approval_id: str = Field(min_length=1)

    class Config:
        extra = "forbid"


class EvolutionTrajectoryEventInput(BaseModel):
    """One trajectory step from HTTP (mirrors :class:`TrajectoryEvent` bounds)."""

    step_type: str = Field(min_length=1, max_length=128)
    payload: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"


class EvolutionTrajectorySubmitRequest(BaseModel):
    """Submit a sanitized trajectory batch when evolution HTTP is enabled."""

    run_id: str = Field(min_length=1, max_length=256)
    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    deployment_mode: str = Field(default="prod", min_length=1)
    events: List[EvolutionTrajectoryEventInput] = Field(default_factory=list, max_length=64)

    class Config:
        extra = "forbid"


class EvalCompareRequest(BaseModel):
    """Compare two persisted eval gate runs."""

    baseline_eval_id: str = Field(min_length=1)
    candidate_eval_id: str = Field(min_length=1)

    class Config:
        extra = "forbid"


class RunCancelRequest(BaseModel):
    """Optional body for cooperative run cancel."""

    force: bool = False

    class Config:
        extra = "forbid"
