"""Unit tests for OutboundOrchestrator: rate limit, quiet hours, safety."""

from __future__ import annotations

from agentium.channels.base import ChannelKind, OutboundMessage
from agentium.channels.null_adapter import NullChannelAdapter
from agentium.channels.outbound_orchestrator import (
    OutboundOrchestrator,
    QuietHours,
    RateLimit,
)
from agentium.coordination.emergence_guardrails import (
    EmergenceGuardrails,
    GuardrailLimit,
)
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.security.dlp_classifier import DLPClassifier
from agentium.security.secret_leak_guard import SecretLeakGuard
from agentium.security.social_engineering_guard import SocialEngineeringGuard


def _orchestrator(
    *,
    rate_limit=None,
    dlp=None,
    secret=None,
    se=None,
    guardrails=None,
    clock=None,
    local_hour=None,
):
    sink = InMemoryAuditSink()
    adapter = NullChannelAdapter()
    orch = OutboundOrchestrator(
        adapters={adapter.name: adapter},
        audit_sink=sink,
        rate_limit=rate_limit,
        dlp_classifier=dlp,
        secret_leak_guard=secret,
        social_engineering_guard=se,
        emergence_guardrails=guardrails,
        clock=clock or (lambda: 1000.0),
        local_hour=local_hour or (lambda: 12),
    )
    return orch, adapter, sink


def _msg(body: str = "hello", subject: str = "hi") -> OutboundMessage:
    return OutboundMessage(
        tenant_id="t1",
        recipient="user@example.com",
        subject=subject,
        body=body,
        kind=ChannelKind.NULL,
    )


def test_orchestrator_dispatches_to_default_channels():
    orch, adapter, _ = _orchestrator()
    result = orch.dispatch(_msg())
    assert len(result.delivered) == 1
    assert result.delivered[0].channel == "null"
    assert len(adapter.sent) == 1


def test_rate_limit_throttles_after_threshold():
    times = iter([1.0, 2.0, 3.0, 4.0])
    orch, _, sink = _orchestrator(
        rate_limit=RateLimit(max_per_window=2, window_seconds=60.0),
        clock=lambda: next(times),
    )
    assert orch.dispatch(_msg()).delivered
    assert orch.dispatch(_msg()).delivered
    blocked = orch.dispatch(_msg())
    assert blocked.skipped and blocked.skipped[0]["reason"] == "rate_limited"
    events = [e.event_type for e in sink.query()]
    assert "channel_skipped" in events


def test_quiet_hours_block_unless_operator_overrides():
    orch, _, _ = _orchestrator(local_hour=lambda: 23)
    orch.set_tenant_quiet_hours("t1", QuietHours(start_hour=22, end_hour=6))
    blocked = orch.dispatch(_msg())
    assert blocked.skipped and blocked.skipped[0]["reason"] == "quiet_hours"
    overridden = orch.dispatch(_msg(), operator_override_quiet_hours=True)
    assert overridden.delivered


def test_dlp_blocks_message_with_secret():
    orch, _, sink = _orchestrator(dlp=DLPClassifier())
    body = "-----BEGIN OPENSSH PRIVATE KEY-----\nXYZ\n-----END OPENSSH PRIVATE KEY-----"
    result = orch.dispatch(_msg(body=body))
    assert not result.delivered
    assert result.failed and "DLP" in result.failed[0]["reason"]
    events = [e.event_type for e in sink.query()]
    assert "dlp_blocked" in events


def test_secret_leak_guard_blocks_high_entropy_token():
    orch, _, sink = _orchestrator(secret=SecretLeakGuard())
    body = "token=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789+ZxCv"
    result = orch.dispatch(_msg(body=body))
    assert not result.delivered
    events = [e.event_type for e in sink.query()]
    assert "secret_leak_blocked" in events


def test_social_engineering_guard_blocks_phishing_body():
    orch, _, sink = _orchestrator(se=SocialEngineeringGuard())
    body = "Please send me your password right now to fix the issue."
    result = orch.dispatch(_msg(body=body))
    assert not result.delivered
    events = [e.event_type for e in sink.query()]
    assert "social_engineering_blocked" in events


def test_guardrail_trip_skips_delivery():
    guardrails = EmergenceGuardrails(
        limits={
            "channel.outbound": GuardrailLimit(
                warn_threshold=1, hard_limit=1
            ),
        },
        clock=lambda: 1.0,
    )
    orch, _, sink = _orchestrator(guardrails=guardrails)
    first = orch.dispatch(_msg())
    assert first.delivered
    second = orch.dispatch(_msg())
    assert second.skipped and second.skipped[0]["reason"] == "guardrail_tripped"


def test_unknown_channel_records_failure():
    orch, _, _ = _orchestrator()
    result = orch.dispatch(_msg(), channels=["nonexistent"])
    assert result.failed and result.failed[0]["reason"] == "unknown_channel"
