"""Email channel adapter using ``smtplib``.

The adapter renders plain-text emails (sufficient for notifications and
approval prompts).  The transport is parameterised so unit tests can inject
an in-memory recorder instead of opening real SMTP sockets.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Callable, Optional

from agentium.channels.base import (
    ChannelAdapter,
    ChannelDeliveryResult,
    ChannelError,
    ChannelKind,
    OutboundMessage,
)


SmtpSendFn = Callable[[EmailMessage], None]


def _default_send(message: EmailMessage) -> None:  # pragma: no cover
    host = message["X-SMTP-Host"]
    port = int(message["X-SMTP-Port"] or 25)
    del message["X-SMTP-Host"]
    del message["X-SMTP-Port"]
    with smtplib.SMTP(host, port) as smtp:
        smtp.send_message(message)


class EmailChannelAdapter(ChannelAdapter):
    """Send messages via SMTP.

    Args:
        sender: ``From:`` address.
        smtp_host / smtp_port: optional transport metadata, embedded into the
            message via ``X-SMTP-*`` headers so injected senders can read them.
        smtp_send: optional callable that performs the actual send.  Tests
            inject a recorder; production code defaults to :func:`_default_send`.
    """

    name = "email"
    kind = ChannelKind.EMAIL

    def __init__(
        self,
        sender: str,
        *,
        smtp_host: str = "localhost",
        smtp_port: int = 25,
        smtp_send: Optional[SmtpSendFn] = None,
    ) -> None:
        if not sender:
            raise ValueError("sender is required")
        self._sender = sender
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_send = smtp_send or _default_send

    def send(self, message: OutboundMessage) -> ChannelDeliveryResult:
        if "@" not in message.recipient:
            raise ChannelError(f"invalid email recipient: {message.recipient!r}")
        email = EmailMessage()
        email["From"] = self._sender
        email["To"] = message.recipient
        email["Subject"] = message.subject or "(no subject)"
        email["X-Tenant-Id"] = message.tenant_id
        if message.run_id:
            email["X-Run-Id"] = message.run_id
        email["X-SMTP-Host"] = self._smtp_host
        email["X-SMTP-Port"] = str(self._smtp_port)
        email.set_content(message.body or "")
        try:
            self._smtp_send(email)
        except Exception as exc:  # pragma: no cover - exercised via injection
            raise ChannelError(f"email transport error: {exc}") from exc
        return ChannelDeliveryResult(
            channel=self.name,
            delivered=True,
            detail={
                "smtp_host": self._smtp_host,
                "smtp_port": self._smtp_port,
                "subject": email["Subject"],
            },
        )


__all__ = ["EmailChannelAdapter"]
