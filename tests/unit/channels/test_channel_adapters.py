"""Unit tests for individual channel adapters."""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from agentium.channels.base import ChannelError, ChannelKind, OutboundMessage
from agentium.channels.email_adapter import EmailChannelAdapter
from agentium.channels.null_adapter import NullChannelAdapter
from agentium.channels.web_adapter import WebChannelAdapter


def _msg(**overrides) -> OutboundMessage:
    base = dict(
        tenant_id="t1",
        recipient="user@example.com",
        subject="hi",
        body="hello",
        kind=ChannelKind.NULL,
    )
    base.update(overrides)
    return OutboundMessage(**base)


def test_null_adapter_records_messages():
    adapter = NullChannelAdapter()
    out = adapter.send(_msg())
    assert out.delivered is True
    assert adapter.sent[0].body == "hello"
    adapter.reset()
    assert adapter.sent == []


def test_web_adapter_serialises_payload_and_handles_status():
    captured: dict[str, object] = {}

    def fake_post(url, body, headers):
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = headers
        return 200

    adapter = WebChannelAdapter(
        webhook_url="https://example.com/hook",
        http_post=fake_post,
        extra_headers={"X-Tenant": "t1"},
    )
    result = adapter.send(_msg(kind=ChannelKind.WEB, body="hello"))
    assert result.delivered
    assert captured["url"] == "https://example.com/hook"
    assert b"hello" in captured["body"]  # type: ignore[operator]
    assert captured["headers"]["X-Tenant"] == "t1"  # type: ignore[index]


def test_web_adapter_raises_on_http_error():
    adapter = WebChannelAdapter(
        webhook_url="https://example.com",
        http_post=lambda url, body, headers: 503,
    )
    with pytest.raises(ChannelError):
        adapter.send(_msg(kind=ChannelKind.WEB))


def test_email_adapter_rejects_invalid_recipient():
    adapter = EmailChannelAdapter(sender="bot@example.com")
    with pytest.raises(ChannelError):
        adapter.send(_msg(recipient="not-an-email"))


def test_email_adapter_invokes_smtp_send_with_message():
    captured: list[EmailMessage] = []

    adapter = EmailChannelAdapter(
        sender="bot@example.com",
        smtp_host="smtp.example",
        smtp_port=587,
        smtp_send=captured.append,
    )
    out = adapter.send(_msg(kind=ChannelKind.EMAIL))
    assert out.delivered
    assert captured and captured[0]["To"] == "user@example.com"
    assert captured[0]["X-Tenant-Id"] == "t1"
