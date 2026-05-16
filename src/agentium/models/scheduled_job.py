"""Domain models for persisted scheduled agent jobs (enterprise control-plane MVP).

Design intent: durable triggers + run ledger separate from short-lived deferred_tasks.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, Self, Union

from pydantic import BaseModel, Field, model_validator


JobRunStatus = Literal[
    "scheduled",
    "claimed",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "timeout",
]

SessionBinding = Literal["pinned_session", "named_persistent", "fresh_each_run"]

TaskKind = Literal["chat_turn"]


class IntervalTriggerSpec(BaseModel):
    """Fixed cadence; interval_seconds lower bound avoids accidental tight loops."""

    kind: Literal["interval"] = "interval"
    interval_seconds: int = Field(ge=60, le=604800)


class OneShotTriggerSpec(BaseModel):
    """Execute once at wall clock (UTC unix epoch milliseconds)."""

    kind: Literal["one_shot"] = "one_shot"
    run_at_unix_ms: int = Field(ge=1)


class CronTriggerSpec(BaseModel):
    """Cron cadence in UTC (requires optional ``croniter`` dependency)."""

    kind: Literal["cron"] = "cron"
    cron_expression: str = Field(min_length=1, max_length=256)
    timezone: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Reserved for future use; scheduler evaluates in UTC only.",
    )

    @model_validator(mode="after")
    def _validate_cron(self) -> Self:
        from agentium.coordination.scheduled_job_schedule import validate_cron_expression

        validate_cron_expression(self.cron_expression)
        return self


TriggerSpec = Annotated[
    Union[IntervalTriggerSpec, OneShotTriggerSpec, CronTriggerSpec],
    Field(discriminator="kind"),
]


class ChatTurnPayload(BaseModel):
    """Payload for ``task_kind == chat_turn``."""

    message_content: str = Field(min_length=1, max_length=32000)
    message_disposition: str = Field(default="collect", max_length=64)
    mcp_execution_tier: str = Field(default="direct-tool", max_length=64)
    enable_agent_tools: bool = False
    agent_skill_override: Optional[str] = Field(default=None, max_length=256)
    auto_ingress: bool = False
    llm_model: Optional[str] = Field(default=None, max_length=128)


def validate_trigger_dict(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize trigger JSON using discriminated union."""

    kind = raw.get("kind")
    if kind == "interval":
        return IntervalTriggerSpec.model_validate(raw).model_dump(mode="json")
    if kind == "one_shot":
        return OneShotTriggerSpec.model_validate(raw).model_dump(mode="json")
    if kind == "cron":
        return CronTriggerSpec.model_validate(raw).model_dump(mode="json")
    raise ValueError(f"unsupported_trigger_kind:{kind!r}")


def validate_chat_turn_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    return ChatTurnPayload.model_validate(raw).model_dump(mode="json")


class ScheduledJobCreateRequest(BaseModel):
    """HTTP: POST /v1/jobs."""

    name: str = Field(min_length=1, max_length=256)
    enabled: bool = True
    task_kind: TaskKind = "chat_turn"
    trigger: Dict[str, Any]
    session_binding: SessionBinding
    pinned_session_id: Optional[str] = Field(default=None, max_length=512)
    payload: Dict[str, Any]
    policy_bundle_ref: Optional[str] = Field(default=None, max_length=512)
    budget_estimate_tokens: Optional[int] = Field(default=None, ge=0, le=2_000_000)
    max_retries: int = Field(default=0, ge=0, le=8)
    timeout_seconds: float = Field(default=120.0, ge=5.0, le=3600.0)

    @model_validator(mode="after")
    def _pinned_consistency(self) -> Self:
        if self.session_binding == "pinned_session":
            if not (self.pinned_session_id or "").strip():
                raise ValueError("pinned_session_id_required_when_binding_is_pinned_session")
        return self


class ScheduledJobPatchRequest(BaseModel):
    """HTTP: PUT /v1/jobs/{job_id} partial update."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=256)
    enabled: Optional[bool] = None
    trigger: Optional[Dict[str, Any]] = None
    session_binding: Optional[SessionBinding] = None
    pinned_session_id: Optional[str] = Field(default=None, max_length=512)
    payload: Optional[Dict[str, Any]] = None
    policy_bundle_ref: Optional[str] = Field(default=None, max_length=512)
    budget_estimate_tokens: Optional[int] = Field(default=None, ge=0, le=2_000_000)
    max_retries: Optional[int] = Field(default=None, ge=0, le=8)
    timeout_seconds: Optional[float] = Field(default=None, ge=5.0, le=3600.0)


class ScheduledJobPublic(BaseModel):
    """Stable JSON shape for API responses."""

    job_id: str
    tenant_id: str
    user_id: str
    name: str
    enabled: bool
    task_kind: TaskKind
    trigger: Dict[str, Any]
    session_binding: SessionBinding
    pinned_session_id: Optional[str]
    payload: Dict[str, Any]
    policy_bundle_ref: Optional[str]
    budget_estimate_tokens: Optional[int]
    max_retries: int
    timeout_seconds: float
    next_run_at_unix_ms: Optional[int]
    last_run_at_unix_ms: Optional[int]
    created_at: str
    updated_at: str


class ScheduledJobRunPublic(BaseModel):
    """One execution attempt."""

    run_id: str
    job_id: str
    tenant_id: str
    status: JobRunStatus
    attempt_no: int
    trace_id: str
    session_id: Optional[str]
    error_detail: Optional[str]
    started_at: str
    finished_at: Optional[str]


class ScheduledJobListResponse(BaseModel):
    items: List[ScheduledJobPublic]
    pagination: Dict[str, Any]


class ScheduledJobRunsResponse(BaseModel):
    items: List[ScheduledJobRunPublic]
    pagination: Dict[str, Any]


__all__ = [
    "ChatTurnPayload",
    "IntervalTriggerSpec",
    "JobRunStatus",
    "CronTriggerSpec",
    "OneShotTriggerSpec",
    "ScheduledJobCreateRequest",
    "ScheduledJobListResponse",
    "ScheduledJobPatchRequest",
    "ScheduledJobPublic",
    "ScheduledJobRunPublic",
    "ScheduledJobRunsResponse",
    "SessionBinding",
    "TaskKind",
    "TriggerSpec",
    "validate_chat_turn_payload",
    "validate_trigger_dict",
]
