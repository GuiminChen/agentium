"""Minimal control-plane API for approval and runtime resume flows."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from agentium.api.runtime_response import RuntimeTurnResponse, map_runtime_result_to_response
from agentium.core.agent_runtime import AgentRuntime
from agentium.core.scheduler import BackpressureError, TenantFairScheduler
from agentium.governance.approval_gate import ApprovalService
from agentium.governance.audit_lineage import AuditSink
from agentium.governance.policy_release import PolicyBundle
from agentium.governance.policy_release_manager import (
    PolicyReleaseManager,
    PolicyReleaseRecord,
)
from agentium.infra.telemetry import NullTelemetry, RuntimeTelemetry
from agentium.models.context import RequestContext


class ApprovalDecisionType(str, Enum):
    """Supported approval decision actions."""

    APPROVE = "approve"
    REJECT = "reject"


class ApprovalStateResponse(BaseModel):
    """Approval request state response payload."""

    approval_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    approver_id: Optional[str] = None
    comment: Optional[str] = None
    args_hash: Optional[str] = None

    class Config:
        extra = "forbid"


class ApprovalDecisionResponse(BaseModel):
    """Approval decision operation response payload."""

    approval_id: str = Field(min_length=1)
    applied: bool
    status: Optional[str] = None

    class Config:
        extra = "forbid"


class AuditEventResponse(BaseModel):
    """Audit event response payload."""

    event_type: str = Field(min_length=1)
    timestamp: datetime
    tenant_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    policy_version: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"


class PolicyReleaseResponse(BaseModel):
    """Policy release state response payload."""

    release_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    status: str = Field(min_length=1)
    requested_by: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    approver_id: Optional[str] = None
    active_tenants: List[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"


class ControlPlaneAPI:
    """Control-plane facade for runtime and HITL approval operations."""

    def __init__(
        self,
        runtime: AgentRuntime,
        approval_service: ApprovalService,
        audit_sink: Optional[AuditSink] = None,
        scheduler: Optional[TenantFairScheduler] = None,
        telemetry: Optional[RuntimeTelemetry] = None,
        policy_release_manager: Optional[PolicyReleaseManager] = None,
    ) -> None:
        self._runtime = runtime
        self._approval_service = approval_service
        self._audit_sink = audit_sink
        self._scheduler = scheduler
        self._telemetry: RuntimeTelemetry = telemetry or NullTelemetry()
        self._policy_release_manager = policy_release_manager

    def run_turn(
        self,
        context: RequestContext,
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
    ) -> RuntimeTurnResponse:
        """Run one turn and map runtime result to API response."""

        self._assert_tenant(context)
        if self._scheduler is not None:
            return self._run_turn_scheduled(context=context, tool_name=tool_name, args=args)
        result = self._runtime.run_turn(context=context, tool_name=tool_name, args=args)
        return map_runtime_result_to_response(result)

    def resume_turn(
        self,
        context: RequestContext,
        tool_name: str,
        approval_id: str,
        args: Optional[Dict[str, Any]] = None,
    ) -> RuntimeTurnResponse:
        """Resume one pending turn after approval."""

        self._assert_tenant(context)
        result = self._runtime.resume_turn(
            context=context,
            tool_name=tool_name,
            approval_id=approval_id,
            args=args,
        )
        return map_runtime_result_to_response(result)

    def _run_turn_scheduled(
        self,
        context: RequestContext,
        tool_name: str,
        args: Optional[Dict[str, Any]],
    ) -> RuntimeTurnResponse:
        if self._scheduler is None:
            raise RuntimeError("scheduler is not configured")
        enqueued_holder: Dict[str, float] = {}
        try:
            job = self._scheduler.submit(
                job_id=context.run_id,
                tenant_id=context.tenant_id,
                work=lambda token: self._run_turn_with_scheduler_attrs(
                    context=context,
                    tool_name=tool_name,
                    args=args,
                    enqueued_at=enqueued_holder["enqueued_at"],
                ),
            )
            enqueued_holder["enqueued_at"] = job.enqueued_at
        except BackpressureError as exc:
            self._telemetry.record_event(
                name="control_plane_backpressure",
                attributes={
                    "tenant_id": context.tenant_id,
                    "run_id": context.run_id,
                    "trace_id": context.trace_id,
                    "tool_name": tool_name,
                    "error_code": "backpressure",
                },
            )
            return RuntimeTurnResponse(
                status="blocked",
                tool_name=tool_name,
                error_code="backpressure",
                message=str(exc),
                references=[],
            )
        self._scheduler.run_pending(max_jobs=1)
        if job.error is not None:
            raise job.error
        if isinstance(job.result, RuntimeTurnResponse):
            return job.result
        raise RuntimeError("scheduled runtime job did not return a RuntimeTurnResponse")

    def _run_turn_with_scheduler_attrs(
        self,
        context: RequestContext,
        tool_name: str,
        args: Optional[Dict[str, Any]],
        enqueued_at: float,
    ) -> RuntimeTurnResponse:
        queue_wait_ms = int((time.monotonic() - enqueued_at) * 1000)
        self._runtime.set_runtime_attributes(
            context.run_id, {"scheduler_queue_wait_ms": max(0, queue_wait_ms)}
        )
        result = self._runtime.run_turn(context=context, tool_name=tool_name, args=args)
        return map_runtime_result_to_response(result)

    def _assert_tenant(self, context: RequestContext) -> None:
        """Reject empty tenant ids and audit the rejection (defense-in-depth)."""

        tenant_id = (context.tenant_id or "").strip()
        if not tenant_id:
            if self._audit_sink is not None:
                try:
                    from agentium.models.context import AuditRecord

                    self._audit_sink.append(
                        AuditRecord(
                            event_type="tenant_missing_blocked",
                            tenant_id="_unknown",
                            run_id=context.run_id,
                            payload={"request_id": context.request_id},
                        )
                    )
                except Exception:
                    pass
            from agentium.shared.errors import PolicyDeniedError

            raise PolicyDeniedError("Empty tenant_id is rejected at control-plane")

    def get_approval(self, approval_id: str) -> Optional[ApprovalStateResponse]:
        """Get approval request state for one approval id."""

        request = self._approval_service.get_request(approval_id)
        if request is None:
            return None
        return ApprovalStateResponse(
            approval_id=request.approval_id,
            status=request.status.value,
            run_id=request.run_id,
            tenant_id=request.tenant_id,
            tool_name=request.tool_name,
            reason=request.reason,
            approver_id=request.approver_id,
            comment=request.comment,
            args_hash=request.args_hash,
        )

    def decide_approval(
        self,
        approval_id: str,
        decision: ApprovalDecisionType,
        approver_id: str,
        comment: str = "",
    ) -> ApprovalDecisionResponse:
        """Apply approval decision and return current state snapshot."""

        previous_state = self._approval_service.get_request(approval_id)
        if decision == ApprovalDecisionType.APPROVE:
            applied = self._approval_service.approve(
                approval_id=approval_id,
                approver_id=approver_id,
                comment=comment,
            )
        else:
            applied = self._approval_service.reject(
                approval_id=approval_id,
                approver_id=approver_id,
                comment=comment,
            )
        state = self._approval_service.get_request(approval_id)
        self._record_hitl_decision(previous_state=previous_state, state=state, applied=applied)
        return ApprovalDecisionResponse(
            approval_id=approval_id,
            applied=applied,
            status=state.status.value if state is not None else None,
        )

    def get_audit_events(
        self,
        run_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditEventResponse]:
        """Query audit events with optional filters."""

        if self._audit_sink is None:
            return []
        records = self._audit_sink.query(run_id=run_id, tenant_id=tenant_id)
        matched = records
        if event_type:
            matched = [record for record in matched if record.event_type == event_type]
        if limit > 0:
            matched = matched[-limit:]
        return [
            AuditEventResponse(
                event_type=record.event_type,
                timestamp=record.timestamp,
                tenant_id=record.tenant_id,
                run_id=record.run_id,
                policy_version=record.policy_version,
                payload=record.payload,
            )
            for record in matched
        ]

    def list_tool_catalog(self) -> Dict[str, Any]:
        """Return registered tools metadata for HTTP catalog (read-only)."""

        tools = self._runtime.tool_registry.list_catalog_entries()
        return {"count": len(tools), "tools": tools}

    def list_approval_states(
        self,
        *,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[ApprovalStateResponse]:
        """List approvals for HTTP centers (tenant/status filters)."""

        rows = self._approval_service.list_approvals(
            tenant_id=tenant_id, status=status, limit=limit
        )
        return [
            ApprovalStateResponse(
                approval_id=request.approval_id,
                status=request.status.value,
                run_id=request.run_id,
                tenant_id=request.tenant_id,
                tool_name=request.tool_name,
                reason=request.reason,
                approver_id=request.approver_id,
                comment=request.comment,
                args_hash=request.args_hash,
            )
            for request in rows
        ]

    def list_run_summaries(self, tenant_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Aggregate latest audit row per run for one tenant."""

        if self._audit_sink is None or not tenant_id:
            return []
        from agentium.infra.db.sqlite_store import SqliteAuditSink

        if isinstance(self._audit_sink, SqliteAuditSink):
            return self._audit_sink.aggregate_recent_runs_for_tenant(tenant_id, limit)
        records = self._audit_sink.query(tenant_id=tenant_id)
        by_run: Dict[str, Any] = {}
        for record in records:
            prev = by_run.get(record.run_id)
            if prev is None or record.timestamp > prev["ts"]:
                by_run[record.run_id] = {
                    "ts": record.timestamp,
                    "event_type": record.event_type,
                }
        summaries = [
            {
                "run_id": rid,
                "last_ts": meta["ts"].isoformat(),
                "last_event_type": meta["event_type"],
            }
            for rid, meta in by_run.items()
        ]
        summaries.sort(key=lambda x: x["last_ts"], reverse=True)
        return summaries[:limit]

    def get_run_timeline(self, run_id: str, limit: int = 500) -> List[AuditEventResponse]:
        """Ordered audit events for one run (UI timeline)."""

        events = self.get_audit_events(
            run_id=run_id, tenant_id=None, event_type=None, limit=limit
        )
        return sorted(events, key=lambda e: e.timestamp)

    def try_list_policy_releases(
        self,
        *,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> Optional[List[PolicyReleaseResponse]]:
        """List policy releases when manager is configured; else None."""

        if self._policy_release_manager is None:
            return None
        records = self._policy_release_manager.list_releases(
            tenant_id=tenant_id, status=status, limit=limit
        )
        return [self._map_policy_release(r) for r in records]

    def submit_policy_release(
        self, bundle: PolicyBundle, context: RequestContext
    ) -> PolicyReleaseResponse:
        """Submit signed policy bundle for release approval."""

        manager = self._require_policy_release_manager()
        record = manager.submit_release(bundle=bundle, context=context)
        return self._map_policy_release(record)

    def approve_policy_release(
        self, release_id: str, approver_id: str, comment: str = ""
    ) -> PolicyReleaseResponse:
        """Approve one policy release request."""

        manager = self._require_policy_release_manager()
        record = manager.approve_release(
            release_id=release_id, approver_id=approver_id, comment=comment
        )
        return self._map_policy_release(record)

    def activate_policy_release(
        self, release_id: str, tenant_ids: List[str], activated_by: str
    ) -> PolicyReleaseResponse:
        """Activate one approved policy release for tenant canary set."""

        manager = self._require_policy_release_manager()
        record = manager.activate_canary(
            release_id=release_id,
            tenant_ids=set(tenant_ids),
            activated_by=activated_by,
        )
        return self._map_policy_release(record)

    def rollback_policy_release(
        self, release_id: str, rolled_back_by: str
    ) -> PolicyReleaseResponse:
        """Rollback one active policy release."""

        manager = self._require_policy_release_manager()
        record = manager.rollback(release_id=release_id, rolled_back_by=rolled_back_by)
        return self._map_policy_release(record)

    def get_policy_release(self, release_id: str) -> Optional[PolicyReleaseResponse]:
        """Get policy release state."""

        manager = self._require_policy_release_manager()
        record = manager.get_release(release_id)
        if record is None:
            return None
        return self._map_policy_release(record)

    def _require_policy_release_manager(self) -> PolicyReleaseManager:
        if self._policy_release_manager is None:
            from agentium.shared.errors import ConfigurationError

            raise ConfigurationError("Policy release manager is not configured")
        return self._policy_release_manager

    @staticmethod
    def _map_policy_release(record: PolicyReleaseRecord) -> PolicyReleaseResponse:
        return PolicyReleaseResponse(
            release_id=record.release_id,
            version=record.bundle.version,
            status=record.status.value,
            requested_by=record.requested_by,
            tenant_id=record.tenant_id,
            run_id=record.run_id,
            approver_id=record.approver_id,
            active_tenants=sorted(record.active_tenants),
        )

    def _record_hitl_decision(
        self,
        previous_state: Optional[object],
        state: Optional[object],
        applied: bool,
    ) -> None:
        if state is None:
            return
        requested_at = getattr(previous_state or state, "requested_at", None)
        wait_ms = 0
        if isinstance(requested_at, datetime):
            wait_ms = int(
                (datetime.now(timezone.utc) - requested_at.astimezone(timezone.utc)).total_seconds()
                * 1000
            )
        self._telemetry.record_event(
            name="hitl_outer_loop_decision",
            attributes={
                "loop_type": "outer_hitl",
                "approval_id": getattr(state, "approval_id", ""),
                "tenant_id": getattr(state, "tenant_id", ""),
                "run_id": getattr(state, "run_id", ""),
                "tool_name": getattr(state, "tool_name", ""),
                "status": getattr(getattr(state, "status", None), "value", ""),
                "applied": applied,
                "approval_wait_ms": max(0, wait_ms),
            },
        )
