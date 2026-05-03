"""NotifyBridge: bridges background-trigger actions to user channels.

Per the technical design (background plane), TriggerPlanner emits *suggestions*
that need to reach the operator without bypassing governance.  The
NotifyBridge adapts these suggestions into :class:`OutboundMessage`
envelopes and dispatches them through the
:class:`OutboundOrchestrator`, so:

- frequency control, quiet hours, DLP, and emergence guardrails apply
  uniformly to background and foreground traffic;
- failed deliveries surface via the orchestrator's audit/telemetry, not
  as silent drops inside the daemon.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional

from agentium.channels.base import ChannelKind, OutboundMessage
from agentium.channels.outbound_orchestrator import (
    OutboundDispatch,
    OutboundOrchestrator,
)


@dataclass(frozen=True)
class NotifyRequest:
    """Structured notification emitted by the background plane / TriggerPlanner."""

    tenant_id: str
    title: str
    body: str
    recipient: str
    run_id: Optional[str] = None
    kind: ChannelKind = ChannelKind.WEB
    channels: List[str] = field(default_factory=list)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    operator_override_quiet_hours: bool = False


class NotifyBridge:
    """Convert :class:`NotifyRequest` instances into outbound deliveries."""

    def __init__(self, orchestrator: OutboundOrchestrator) -> None:
        self._orchestrator = orchestrator

    def notify(self, request: NotifyRequest) -> OutboundDispatch:
        message = OutboundMessage(
            tenant_id=request.tenant_id,
            recipient=request.recipient,
            subject=request.title,
            body=request.body,
            kind=request.kind,
            run_id=request.run_id,
            metadata=request.metadata,
        )
        return self._orchestrator.dispatch(
            message,
            channels=request.channels or None,
            operator_override_quiet_hours=request.operator_override_quiet_hours,
        )


__all__ = ["NotifyBridge", "NotifyRequest"]
