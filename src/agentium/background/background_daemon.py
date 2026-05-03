"""Background-plane daemon for proactive runtime safety actions (governed outer loop)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, List, Optional

from agentium.background.event_ingestor import EventIngestor
from agentium.background.memory_consolidator import (
    ConsolidationReport,
    MemoryConsolidator,
)
from agentium.background.notify_bridge import NotifyBridge, NotifyRequest
from agentium.background.policy_guard import BackgroundPolicyGuard
from agentium.background.trigger_planner import (
    PlannerResult,
    ProposedAction,
    TriggerPlanner,
)
from agentium.background.triggers import (
    CallbackTrigger,
    IntervalTrigger,
    TriggerEvent,
    utc_now,
)
from agentium.channels.base import ChannelKind
from agentium.governance.approval_gate import ApprovalService
from agentium.governance.audit_lineage import AuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.models.context import AuditRecord, RequestContext


_LOGGER = logging.getLogger("agentium.background.daemon")


@dataclass
class FullTickReport:
    """Result of :meth:`BackgroundDaemon.tick_full`."""

    tick_fired: int = 0
    events_drained: int = 0
    suggestions: List[ProposedAction] = None  # type: ignore[assignment]
    approval_required: List[ProposedAction] = None  # type: ignore[assignment]
    dispatched: List[str] = None  # type: ignore[assignment]
    pending_approvals: List[str] = None  # type: ignore[assignment]
    consolidation: Optional[ConsolidationReport] = None

    def __post_init__(self) -> None:
        if self.suggestions is None:
            self.suggestions = []
        if self.approval_required is None:
            self.approval_required = []
        if self.dispatched is None:
            self.dispatched = []
        if self.pending_approvals is None:
            self.pending_approvals = []


@dataclass
class BackgroundTriggerHandler:
    """One bound trigger + handler invoked when the trigger fires."""

    trigger_name: str
    handler: Callable[[TriggerEvent], None]


class BackgroundDaemon:
    """Periodic background daemon that enforces safety hooks.

    Responsibilities:
    - Sweep approval expirations and emit audit events.
    - Run interval/callback triggers, gated by BackgroundPolicyGuard.
    - Provide pause/resume to support the "one-button stop" requirement.
    """

    def __init__(
        self,
        approval_service: ApprovalService,
        audit_sink: AuditSink,
        policy_engine: PolicyEngine,
        interval_seconds: float = 30.0,
        *,
        event_ingestor: Optional[EventIngestor] = None,
        trigger_planner: Optional[TriggerPlanner] = None,
        memory_consolidator: Optional[MemoryConsolidator] = None,
        notify_bridge: Optional[NotifyBridge] = None,
        consolidation_context_factory: Optional[
            Callable[[], "RequestContext"]
        ] = None,
        noise_rps_pause: Optional[float] = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._approval_service = approval_service
        self._audit_sink = audit_sink
        self._guard = BackgroundPolicyGuard(policy_engine)
        self._interval = interval_seconds
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._interval_triggers: List[IntervalTrigger] = []
        self._callback_triggers: List[CallbackTrigger] = []
        self._handlers: List[BackgroundTriggerHandler] = []
        self._ingestor = event_ingestor
        self._planner = trigger_planner
        self._consolidator = memory_consolidator
        self._notify_bridge = notify_bridge
        self._consolidation_ctx_factory = consolidation_context_factory
        self._noise_rps_pause = (
            noise_rps_pause if noise_rps_pause is not None and noise_rps_pause > 0 else None
        )

    def add_interval_trigger(
        self,
        trigger: IntervalTrigger,
        handler: Callable[[TriggerEvent], None],
    ) -> None:
        """Register an interval trigger with its handler."""

        self._interval_triggers.append(trigger)
        self._handlers.append(
            BackgroundTriggerHandler(trigger_name=trigger.name, handler=handler)
        )

    def add_callback_trigger(
        self,
        trigger: CallbackTrigger,
        handler: Callable[[TriggerEvent], None],
    ) -> None:
        """Register a callback trigger with its handler."""

        self._callback_triggers.append(trigger)
        self._handlers.append(
            BackgroundTriggerHandler(trigger_name=trigger.name, handler=handler)
        )

    def start(self) -> None:
        """Start the background sweep thread."""

        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="agentium-background", daemon=True
        )
        self._thread.start()

    def stop(self, timeout_seconds: float = 5.0) -> None:
        """Stop the background sweep thread with a join timeout."""

        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout_seconds)
        self._thread = None

    def pause(self) -> None:
        """Pause trigger handling without stopping the sweep loop."""

        self._pause_event.set()
        self._audit_safety_event("background_paused", {})

    def resume(self) -> None:
        """Resume trigger handling after a pause."""

        if self._pause_event.is_set():
            self._pause_event.clear()
            self._audit_safety_event("background_resumed", {})

    @property
    def paused(self) -> bool:
        """Return whether trigger handling is currently paused."""

        return self._pause_event.is_set()

    def tick(self) -> int:
        """Run one synchronous sweep iteration. Returns number of fired events."""

        fired = 0
        try:
            expired = self._approval_service.expire_pending()
        except Exception as exc:
            _LOGGER.exception("background approval expiration failed: %s", exc)
            expired = []
        for request in expired:
            self._audit_safety_event(
                "background_approval_expired",
                {
                    "approval_id": request.approval_id,
                    "run_id": request.run_id,
                    "tenant_id": request.tenant_id,
                    "tool_name": request.tool_name,
                },
            )
        if self.paused:
            return fired
        now = utc_now()
        for trigger in self._interval_triggers:
            if trigger.should_fire(now):
                event = trigger.mark_fired(now)
                fired += int(self._dispatch(event))
        for trigger in self._callback_triggers:
            event = trigger.maybe_fire(now)
            if event is not None:
                fired += int(self._dispatch(event))
        return fired

    def tick_full(self) -> "FullTickReport":
        """Run a comprehensive sweep: events, planning, dispatch, consolidation.

        This is invoked by integration tests and by the main loop when all
        Phase 6 submodules are wired in.  Each step is independently
        guarded; failures in one branch never block the others.
        """

        report = FullTickReport()
        report.tick_fired = self.tick()

        if self._ingestor is not None and self._planner is not None:
            try:
                events = self._ingestor.drain()
            except Exception as exc:  # pragma: no cover - defensive
                _LOGGER.exception("background event drain failed: %s", exc)
                events = []
            report.events_drained = len(events)
            if (
                self._noise_rps_pause is not None
                and self._ingestor is not None
                and self._ingestor.submissions_in_window() >= self._noise_rps_pause
            ):
                self._audit_safety_event(
                    "background_noise_tripwire",
                    {
                        "submissions_in_window": self._ingestor.submissions_in_window(),
                        "configured_rps_pause": self._noise_rps_pause,
                    },
                )
                self.pause()
            try:
                planner_result = self._planner.plan(events)
            except Exception as exc:  # pragma: no cover - defensive
                _LOGGER.exception("background planner failed: %s", exc)
                planner_result = PlannerResult()
            report.suggestions = planner_result.suggestions
            report.approval_required = planner_result.approval_required
            for action in planner_result.suggestions:
                self._maybe_dispatch_suggestion(action, report)
            for action in planner_result.approval_required:
                self._record_pending_approval(action, report)

        if self._consolidator is not None and self._consolidation_ctx_factory is not None:
            try:
                ctx = self._consolidation_ctx_factory()
                report.consolidation = self._consolidator.consolidate(ctx)
            except Exception as exc:  # pragma: no cover - defensive
                _LOGGER.exception("background memory consolidation failed: %s", exc)
                self._audit_safety_event(
                    "background_consolidation_failed", {"error": str(exc)}
                )
        return report

    def _maybe_dispatch_suggestion(
        self, action: ProposedAction, report: "FullTickReport"
    ) -> None:
        if self._notify_bridge is None:
            return
        try:
            kind_value = action.payload.get("channel_kind")
            if isinstance(kind_value, ChannelKind):
                kind = kind_value
            elif isinstance(kind_value, str):
                try:
                    kind = ChannelKind(kind_value)
                except ValueError:
                    kind = ChannelKind.WEB
            else:
                kind = ChannelKind.WEB
            request = NotifyRequest(
                tenant_id=action.tenant_id,
                title=action.action,
                body=action.summary,
                recipient=str(
                    action.payload.get("recipient") or "agentium-background://broadcast"
                ),
                kind=kind,
                run_id=str(action.payload.get("run_id") or "background"),
                metadata={"risk": action.risk, "topic": action.source_event.topic},
            )
            dispatch = self._notify_bridge.notify(request)
            report.dispatched.append(action.action)
            self._audit_safety_event(
                "background_suggestion_dispatched",
                {
                    "action": action.action,
                    "delivered": bool(getattr(dispatch, "delivered", [])),
                },
                tenant_id=action.tenant_id,
            )
        except Exception as exc:
            self._audit_safety_event(
                "background_suggestion_failed",
                {"action": action.action, "error": str(exc)},
                tenant_id=action.tenant_id,
            )

    def _record_pending_approval(
        self, action: ProposedAction, report: "FullTickReport"
    ) -> None:
        report.pending_approvals.append(action.action)
        self._audit_safety_event(
            "background_action_requires_approval",
            {
                "action": action.action,
                "risk": action.risk,
                "topic": action.source_event.topic,
            },
            tenant_id=action.tenant_id,
        )

    def _dispatch(self, event: TriggerEvent) -> bool:
        handler = next(
            (h for h in self._handlers if h.trigger_name == event.name), None
        )
        if handler is None:
            return False
        try:
            handler.handler(event)
            return True
        except Exception as exc:
            self._audit_safety_event(
                "background_misfire_blocked",
                {"trigger": event.name, "error": str(exc)},
            )
            return False

    def evaluate_action(
        self,
        context: RequestContext,
        tool_name: str,
        args: Optional[dict] = None,
    ) -> bool:
        """Validate a proposed background-plane action through the policy guard."""

        decision = self._guard.check(context=context, tool_name=tool_name, call_args=args)
        if not decision.allowed:
            self._audit_safety_event(
                "background_action_blocked",
                {
                    "tool_name": tool_name,
                    "reason": decision.reason,
                    "rule_id": decision.rule_id,
                },
                tenant_id=context.tenant_id,
                run_id=context.run_id,
            )
        return decision.allowed

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                _LOGGER.exception("background tick failed: %s", exc)
            self._stop_event.wait(self._interval)

    def _audit_safety_event(
        self,
        event_type: str,
        payload: dict,
        tenant_id: str = "_background",
        run_id: str = "_background",
    ) -> None:
        try:
            self._audit_sink.append(
                AuditRecord(
                    event_type=event_type,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    payload=payload,
                )
            )
        except Exception:
            pass
