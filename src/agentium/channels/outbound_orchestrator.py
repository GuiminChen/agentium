"""Outbound orchestrator: rate-limited fan-out across channel adapters.

Responsibilities:

1. **Per-tenant frequency control** – sliding window prevents runaway
   notifications and protects downstream channels.
2. **Quiet hours** – tenants may declare time windows where automatic
   sends are deferred; manual operator sends bypass this gate explicitly.
3. **Pre-send safety** – wires :class:`DLPClassifier`,
   :class:`SecretLeakGuard`, and :class:`SocialEngineeringGuard` so the
   `Channel → ... → ChannelSend` pipeline (PRD §3.16) is enforced even
   when callers bypass :class:`ToolRegistry`.
4. **Audit/Telemetry** – every decision is appended to the audit sink and
   reflected in the runtime telemetry so operators can investigate later.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Mapping, Optional

from agentium.channels.base import (
    ChannelAdapter,
    ChannelDeliveryResult,
    ChannelError,
    OutboundMessage,
)
from agentium.coordination.emergence_guardrails import (
    EmergenceGuardrails,
    GuardrailState,
)
from agentium.governance.audit_lineage import AuditSink
from agentium.infra.telemetry import NullTelemetry, RuntimeTelemetry
from agentium.models.context import AuditRecord
from agentium.security.dlp_classifier import DLPClassifier
from agentium.security.secret_leak_guard import SecretLeakGuard
from agentium.security.social_engineering_guard import SocialEngineeringGuard
from agentium.shared.errors import PolicyDeniedError


@dataclass(frozen=True)
class RateLimit:
    """Sliding-window rate limit."""

    max_per_window: int
    window_seconds: float

    def __post_init__(self) -> None:
        if self.max_per_window <= 0:
            raise ValueError("max_per_window must be positive")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")


@dataclass(frozen=True)
class QuietHours:
    """Half-open quiet window in 24h local hours.

    ``start_hour`` inclusive, ``end_hour`` exclusive.  When ``start_hour``
    > ``end_hour`` (overnight quiet), the window wraps midnight.
    """

    start_hour: int
    end_hour: int

    def is_quiet(self, hour: int) -> bool:
        if self.start_hour == self.end_hour:
            return False
        if self.start_hour < self.end_hour:
            return self.start_hour <= hour < self.end_hour
        return hour >= self.start_hour or hour < self.end_hour


@dataclass
class OutboundDispatch:
    """Aggregate result returned by :meth:`OutboundOrchestrator.dispatch`."""

    delivered: List[ChannelDeliveryResult] = field(default_factory=list)
    skipped: List[Dict[str, str]] = field(default_factory=list)
    failed: List[Dict[str, str]] = field(default_factory=list)


class OutboundOrchestrator:
    """Coordinate outbound delivery across registered channel adapters.

    Args:
        adapters: registered channel adapters keyed by their stable name.
        audit_sink: governance audit sink for compliance traces.
        telemetry: runtime telemetry; defaults to :class:`NullTelemetry`.
        rate_limit: default rate limit applied per tenant.  ``None`` disables
            global throttling, but tenants can still override via
            :meth:`set_tenant_rate_limit`.
        dlp_classifier / secret_leak_guard / social_engineering_guard:
            optional safety scanners run before each send.
        emergence_guardrails: optional cross-run guardrail; the orchestrator
            increments the ``channel.outbound`` counter on every attempt.
    """

    def __init__(
        self,
        adapters: Mapping[str, ChannelAdapter],
        audit_sink: AuditSink,
        *,
        telemetry: Optional[RuntimeTelemetry] = None,
        rate_limit: Optional[RateLimit] = None,
        dlp_classifier: Optional[DLPClassifier] = None,
        secret_leak_guard: Optional[SecretLeakGuard] = None,
        social_engineering_guard: Optional[SocialEngineeringGuard] = None,
        emergence_guardrails: Optional[EmergenceGuardrails] = None,
        clock: Callable[[], float] = time.time,
        local_hour: Optional[Callable[[], int]] = None,
    ) -> None:
        if not adapters:
            raise ValueError("at least one channel adapter is required")
        self._adapters = dict(adapters)
        self._audit = audit_sink
        self._telemetry: RuntimeTelemetry = telemetry or NullTelemetry()
        self._default_limit = rate_limit
        self._dlp = dlp_classifier
        self._secret_guard = secret_leak_guard
        self._se_guard = social_engineering_guard
        self._guardrails = emergence_guardrails
        self._clock = clock
        self._local_hour = local_hour or (lambda: time.localtime().tm_hour)
        self._tenant_limits: Dict[str, RateLimit] = {}
        self._tenant_quiet: Dict[str, QuietHours] = {}
        self._history: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def set_tenant_rate_limit(self, tenant_id: str, limit: RateLimit) -> None:
        with self._lock:
            self._tenant_limits[tenant_id] = limit

    def set_tenant_quiet_hours(self, tenant_id: str, quiet: QuietHours) -> None:
        with self._lock:
            self._tenant_quiet[tenant_id] = quiet

    def dispatch(
        self,
        message: OutboundMessage,
        *,
        channels: Optional[List[str]] = None,
        operator_override_quiet_hours: bool = False,
    ) -> OutboundDispatch:
        """Send ``message`` across the selected channels."""

        result = OutboundDispatch()
        if not operator_override_quiet_hours and self._is_quiet(message.tenant_id):
            self._record_skipped(message, reason="quiet_hours")
            result.skipped.append({"reason": "quiet_hours", "tenant_id": message.tenant_id})
            return result

        if not self._consume_rate_token(message.tenant_id):
            self._record_skipped(message, reason="rate_limited")
            result.skipped.append({"reason": "rate_limited", "tenant_id": message.tenant_id})
            return result

        try:
            self._enforce_safety(message)
        except PolicyDeniedError as exc:
            result.failed.append({"reason": str(exc), "tenant_id": message.tenant_id})
            return result

        if self._guardrails is not None:
            decision = self._guardrails.try_increment(
                counter="channel.outbound",
                tenant_id=message.tenant_id,
                scope_id=message.tenant_id,
            )
            if decision.state == GuardrailState.TRIPPED:
                self._record_skipped(message, reason="guardrail_tripped")
                result.skipped.append(
                    {
                        "reason": "guardrail_tripped",
                        "counter": decision.counter,
                        "tenant_id": message.tenant_id,
                    }
                )
                return result

        targets = channels or list(self._adapters.keys())
        for name in targets:
            adapter = self._adapters.get(name)
            if adapter is None:
                result.failed.append({"channel": name, "reason": "unknown_channel"})
                continue
            try:
                outcome = adapter.send(message)
                result.delivered.append(outcome)
                self._record_delivered(message, name, outcome)
            except ChannelError as exc:
                result.failed.append({"channel": name, "reason": str(exc)})
                self._record_failed(message, name, str(exc))
        return result

    def _consume_rate_token(self, tenant_id: str) -> bool:
        limit = self._tenant_limits.get(tenant_id, self._default_limit)
        if limit is None:
            return True
        now = self._clock()
        cutoff = now - limit.window_seconds
        with self._lock:
            window = self._history[tenant_id]
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) >= limit.max_per_window:
                return False
            window.append(now)
            return True

    def _is_quiet(self, tenant_id: str) -> bool:
        quiet = self._tenant_quiet.get(tenant_id)
        if quiet is None:
            return False
        return quiet.is_quiet(self._local_hour())

    def _enforce_safety(self, message: OutboundMessage) -> None:
        scan_payload = {
            "subject": message.subject,
            "body": message.body,
            "metadata": dict(message.metadata),
        }
        if self._dlp is not None:
            decision = self._dlp.classify_payload(scan_payload)
            if decision.blocked:
                self._record_blocked(message, "dlp_blocked", {
                    "labels": sorted({h.label for h in decision.hits})
                })
                raise PolicyDeniedError("DLP blocked outbound message")
        if self._secret_guard is not None:
            secret_decision = self._secret_guard.scan_payload(scan_payload)
            if secret_decision.blocked:
                self._record_blocked(message, "secret_leak_blocked", {
                    "hit_count": len(secret_decision.hits)
                })
                raise PolicyDeniedError("Secret leak guard blocked outbound message")
        if self._se_guard is not None:
            se_decision = self._se_guard.classify(message.body or "")
            if se_decision.blocked:
                self._record_blocked(message, "social_engineering_blocked", {
                    "labels": sorted({h.label for h in se_decision.hits}),
                    "severity": se_decision.severity,
                })
                raise PolicyDeniedError("Social engineering guard blocked outbound message")

    def _record_delivered(
        self,
        message: OutboundMessage,
        channel: str,
        outcome: ChannelDeliveryResult,
    ) -> None:
        self._audit.append(
            AuditRecord(
                event_type="channel_delivered",
                tenant_id=message.tenant_id,
                run_id=message.run_id or "outbound",
                policy_version="outbound",
                payload={
                    "channel": channel,
                    "recipient": message.recipient,
                    "subject": message.subject,
                    "transport_id": outcome.transport_id,
                },
            )
        )
        self._telemetry.record_event(
            name="channel_delivered",
            attributes={
                "tenant_id": message.tenant_id,
                "channel": channel,
                "kind": message.kind.value,
            },
        )

    def _record_skipped(self, message: OutboundMessage, *, reason: str) -> None:
        self._audit.append(
            AuditRecord(
                event_type="channel_skipped",
                tenant_id=message.tenant_id,
                run_id=message.run_id or "outbound",
                policy_version="outbound",
                payload={"reason": reason, "recipient": message.recipient},
            )
        )
        self._telemetry.record_event(
            name="channel_skipped",
            attributes={"tenant_id": message.tenant_id, "reason": reason},
        )

    def _record_failed(self, message: OutboundMessage, channel: str, reason: str) -> None:
        self._audit.append(
            AuditRecord(
                event_type="channel_failed",
                tenant_id=message.tenant_id,
                run_id=message.run_id or "outbound",
                policy_version="outbound",
                payload={"channel": channel, "reason": reason},
            )
        )
        self._telemetry.record_event(
            name="channel_failed",
            attributes={"tenant_id": message.tenant_id, "channel": channel},
        )

    def _record_blocked(
        self,
        message: OutboundMessage,
        event_type: str,
        detail: Mapping[str, object],
    ) -> None:
        self._audit.append(
            AuditRecord(
                event_type=event_type,
                tenant_id=message.tenant_id,
                run_id=message.run_id or "outbound",
                policy_version="outbound",
                payload={"recipient": message.recipient, **detail},
            )
        )
        self._telemetry.record_event(
            name=event_type,
            attributes={"tenant_id": message.tenant_id},
        )


__all__ = [
    "OutboundDispatch",
    "OutboundOrchestrator",
    "QuietHours",
    "RateLimit",
]
