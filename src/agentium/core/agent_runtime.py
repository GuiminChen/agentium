"""Minimal runtime loop for controlled tool execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from agentium.core.run_cancellation import RunCancelRegistry

from agentium.core.agent_lifecycle import AgentLifecycleError, AgentLifecycleManager
from agentium.infra.telemetry import NullTelemetry, RuntimeTelemetry
from agentium.models.context import RequestContext
from agentium.shared.errors import (
    ApprovalRequiredError,
    BudgetExceededError,
    PolicyDeniedError,
)
from agentium.shared.request_context import set_request_context
from agentium.tools.tool_registry import ToolExecutionResult, ToolRegistry


class RuntimeStatus(str, Enum):
    """Runtime turn status."""

    COMPLETED = "completed"
    PENDING_APPROVAL = "pending_approval"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class RuntimeResult:
    """Result for one minimal runtime turn."""

    status: RuntimeStatus
    tool_name: str
    output: Optional[Dict[str, Any]] = None
    tool_use_id: Optional[str] = None
    approval_id: Optional[str] = None
    message: Optional[str] = None
    error_code: Optional[str] = None


class AgentRuntime:
    """Minimal runtime that executes one tool call per turn."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        telemetry: Optional[RuntimeTelemetry] = None,
        lifecycle_manager: Optional[AgentLifecycleManager] = None,
        run_cancel_registry: Optional["RunCancelRegistry"] = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._telemetry: RuntimeTelemetry = telemetry or NullTelemetry()
        self._lifecycle_manager = lifecycle_manager
        self._run_cancel_registry = run_cancel_registry
        self._runtime_attributes_by_run: Dict[str, Dict[str, Any]] = {}
        self._attributes_lock = Lock()

    @property
    def tool_registry(self) -> ToolRegistry:
        """Tool registry for control-plane read-only introspection."""

        return self._tool_registry

    def set_runtime_attributes(self, run_id: str, attributes: Dict[str, Any]) -> None:
        """Attach control-plane attributes to the next telemetry event for a run."""

        with self._attributes_lock:
            current = self._runtime_attributes_by_run.setdefault(run_id, {})
            current.update(attributes)

    def run_turn(
        self,
        context: RequestContext,
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
    ) -> RuntimeResult:
        """Run one turn by executing one tool via ToolRegistry."""

        span_attrs = {
            "run_id": context.run_id,
            "trace_id": context.trace_id,
            "tenant_id": context.tenant_id,
        }
        with self._telemetry.start_span("agentium.turn.run", attributes=span_attrs):
            return self._run_turn_inner(context, tool_name, args)

    def _run_turn_inner(
        self,
        context: RequestContext,
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
    ) -> RuntimeResult:
        set_request_context(context)
        if self._run_cancel_registry is not None and self._run_cancel_registry.is_cancelled(
            context.run_id
        ):
            result_model = RuntimeResult(
                status=RuntimeStatus.BLOCKED,
                tool_name=tool_name,
                message="run was cancelled",
                error_code="cancelled",
            )
            self._telemetry.record_runtime_turn(
                status=result_model.status.value,
                error_code=result_model.error_code,
                attributes={
                    "turn_type": "run_turn",
                    "tenant_id": context.tenant_id,
                    "run_id": context.run_id,
                    "trace_id": context.trace_id,
                    "tool_name": tool_name,
                    "tool_use_id": "",
                    "lifecycle_state": "",
                },
            )
            return result_model
        self._start_lifecycle(context)
        result_model: RuntimeResult
        try:
            result: ToolExecutionResult = self._tool_registry.execute(
                context=context, name=tool_name, args=args
            )
            lifecycle_state = self._finish_lifecycle(context)
            result_model = RuntimeResult(
                status=RuntimeStatus.COMPLETED,
                tool_name=tool_name,
                output=result.output,
                tool_use_id=result.call_record.tool_use_id,
            )
        except ApprovalRequiredError as exc:
            result_model = RuntimeResult(
                status=RuntimeStatus.PENDING_APPROVAL,
                tool_name=tool_name,
                approval_id=exc.approval_id,
                message=str(exc),
                error_code="approval_required",
            )
            lifecycle_state = self._block_lifecycle_hitl(context, str(exc))
        except PolicyDeniedError as exc:
            result_model = RuntimeResult(
                status=RuntimeStatus.BLOCKED,
                tool_name=tool_name,
                message=str(exc),
                error_code="policy_denied",
            )
            lifecycle_state = self._fail_lifecycle(context, str(exc))
        except BudgetExceededError as exc:
            result_model = RuntimeResult(
                status=RuntimeStatus.BLOCKED,
                tool_name=tool_name,
                message=str(exc),
                error_code="budget_exceeded",
            )
            lifecycle_state = self._fail_lifecycle(context, str(exc))
            self._telemetry.record_quota_hard_limit_trigger(
                {
                    "tenant_id": context.tenant_id,
                    "run_id": context.run_id,
                    "trace_id": context.trace_id,
                    "tool_name": tool_name,
                    "turn_type": "run_turn",
                    "trigger": "budget_exceeded",
                }
            )
        except Exception as exc:
            result_model = RuntimeResult(
                status=RuntimeStatus.BLOCKED,
                tool_name=tool_name,
                message="safe_degrade: unexpected runtime error blocked",
                error_code="internal_error",
            )
            lifecycle_state = self._fail_lifecycle(context, str(exc))
            self._telemetry.record_event(
                name="runtime_safe_degrade",
                attributes={
                    "tenant_id": context.tenant_id,
                    "run_id": context.run_id,
                    "trace_id": context.trace_id,
                    "tool_name": tool_name,
                    "error_type": exc.__class__.__name__,
                },
            )
        telemetry_attributes = {
            "turn_type": "run_turn",
            "tenant_id": context.tenant_id,
            "run_id": context.run_id,
            "trace_id": context.trace_id,
            "tool_name": tool_name,
            "tool_use_id": result_model.tool_use_id or "",
            "lifecycle_state": lifecycle_state,
        }
        telemetry_attributes.update(self._pop_runtime_attributes(context.run_id))
        self._telemetry.record_runtime_turn(
            status=result_model.status.value,
            error_code=result_model.error_code,
            attributes=telemetry_attributes,
        )
        return result_model

    def resume_turn(
        self,
        context: RequestContext,
        tool_name: str,
        approval_id: str,
        args: Optional[Dict[str, Any]] = None,
    ) -> RuntimeResult:
        """Resume one pending turn after approval decision."""

        span_attrs = {
            "run_id": context.run_id,
            "trace_id": context.trace_id,
            "tenant_id": context.tenant_id,
            "approval_id": approval_id,
        }
        with self._telemetry.start_span("agentium.turn.resume", attributes=span_attrs):
            return self._resume_turn_inner(context, tool_name, approval_id, args)

    def _resume_turn_inner(
        self,
        context: RequestContext,
        tool_name: str,
        approval_id: str,
        args: Optional[Dict[str, Any]] = None,
    ) -> RuntimeResult:
        set_request_context(context)
        if self._run_cancel_registry is not None and self._run_cancel_registry.is_cancelled(
            context.run_id
        ):
            result_model = RuntimeResult(
                status=RuntimeStatus.BLOCKED,
                tool_name=tool_name,
                message="run was cancelled",
                error_code="cancelled",
            )
            self._telemetry.record_runtime_turn(
                status=result_model.status.value,
                error_code=result_model.error_code,
                attributes={
                    "turn_type": "resume_turn",
                    "tenant_id": context.tenant_id,
                    "run_id": context.run_id,
                    "trace_id": context.trace_id,
                    "tool_name": tool_name,
                    "tool_use_id": "",
                    "approval_id": approval_id,
                    "lifecycle_state": "",
                },
            )
            return result_model
        self._start_lifecycle(context)
        result_model: RuntimeResult
        try:
            result: ToolExecutionResult = self._tool_registry.execute_after_approval(
                context=context,
                name=tool_name,
                approval_id=approval_id,
                args=args,
            )
            lifecycle_state = self._finish_lifecycle(context)
            result_model = RuntimeResult(
                status=RuntimeStatus.COMPLETED,
                tool_name=tool_name,
                output=result.output,
                tool_use_id=result.call_record.tool_use_id,
            )
        except ApprovalRequiredError as exc:
            result_model = RuntimeResult(
                status=RuntimeStatus.PENDING_APPROVAL,
                tool_name=tool_name,
                approval_id=exc.approval_id,
                message=str(exc),
                error_code="approval_required",
            )
            lifecycle_state = self._block_lifecycle_hitl(context, str(exc))
        except PolicyDeniedError as exc:
            result_model = RuntimeResult(
                status=RuntimeStatus.BLOCKED,
                tool_name=tool_name,
                message=str(exc),
                error_code="policy_denied",
            )
            lifecycle_state = self._fail_lifecycle(context, str(exc))
        except BudgetExceededError as exc:
            result_model = RuntimeResult(
                status=RuntimeStatus.BLOCKED,
                tool_name=tool_name,
                message=str(exc),
                error_code="budget_exceeded",
            )
            lifecycle_state = self._fail_lifecycle(context, str(exc))
            self._telemetry.record_quota_hard_limit_trigger(
                {
                    "tenant_id": context.tenant_id,
                    "run_id": context.run_id,
                    "trace_id": context.trace_id,
                    "tool_name": tool_name,
                    "approval_id": approval_id,
                    "turn_type": "resume_turn",
                    "trigger": "budget_exceeded",
                }
            )
        except Exception as exc:
            result_model = RuntimeResult(
                status=RuntimeStatus.BLOCKED,
                tool_name=tool_name,
                message="safe_degrade: unexpected runtime error blocked",
                error_code="internal_error",
            )
            lifecycle_state = self._fail_lifecycle(context, str(exc))
            self._telemetry.record_event(
                name="runtime_safe_degrade",
                attributes={
                    "tenant_id": context.tenant_id,
                    "run_id": context.run_id,
                    "trace_id": context.trace_id,
                    "tool_name": tool_name,
                    "approval_id": approval_id,
                    "error_type": exc.__class__.__name__,
                },
            )
        telemetry_attributes = {
            "turn_type": "resume_turn",
            "tenant_id": context.tenant_id,
            "run_id": context.run_id,
            "trace_id": context.trace_id,
            "tool_name": tool_name,
            "tool_use_id": result_model.tool_use_id or "",
            "approval_id": approval_id,
            "lifecycle_state": lifecycle_state,
        }
        telemetry_attributes.update(self._pop_runtime_attributes(context.run_id))
        self._telemetry.record_runtime_turn(
            status=result_model.status.value,
            error_code=result_model.error_code,
            attributes=telemetry_attributes,
        )
        return result_model

    def _pop_runtime_attributes(self, run_id: str) -> Dict[str, Any]:
        with self._attributes_lock:
            return self._runtime_attributes_by_run.pop(run_id, {})

    def _start_lifecycle(self, context: RequestContext) -> None:
        if self._lifecycle_manager is None:
            return
        try:
            self._lifecycle_manager.get(context.run_id)
        except AgentLifecycleError:
            self._lifecycle_manager.create(context)
        self._lifecycle_manager.ready(context.run_id)
        self._lifecycle_manager.start(context.run_id)

    def _finish_lifecycle(self, context: RequestContext) -> str:
        if self._lifecycle_manager is None:
            return ""
        self._lifecycle_manager.stop(context.run_id)
        return self._lifecycle_manager.cleanup(context.run_id).state.value

    def _block_lifecycle_hitl(self, context: RequestContext, reason: str) -> str:
        if self._lifecycle_manager is None:
            return ""
        return self._lifecycle_manager.block_hitl(context.run_id, reason).state.value

    def _fail_lifecycle(self, context: RequestContext, reason: str) -> str:
        if self._lifecycle_manager is None:
            return ""
        failed = self._lifecycle_manager.fail(context.run_id, reason)
        self._lifecycle_manager.cleanup(context.run_id, reason)
        return failed.state.value
