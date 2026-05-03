"""Channel adapter base contract.

A *channel* is any external surface the agent uses to deliver outbound
messages: web UI, email, IM bridges, CLI notifications.  Adapters share a
single contract so the :class:`OutboundOrchestrator` can route notifications
without leaking transport details into the runtime.

PRD references:

- §3.5–3.6 (Interfaces, Deployment, External outreach)
- §3.16 (`on_channel_send` hooks for DLP / policy)
- Technical design §ChannelPlane
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional


class ChannelKind(str, Enum):
    """Stable, audit-friendly channel categories."""

    WEB = "web"
    EMAIL = "email"
    NULL = "null"
    CLI = "cli"


@dataclass(frozen=True)
class OutboundMessage:
    """Normalised payload handed to a channel adapter.

    Attributes:
        tenant_id: caller tenant.  Used for rate limit accounting.
        run_id: control-plane run id.  Optional for operator-initiated sends.
        recipient: channel-specific recipient (email, user id, webhook url).
        subject: short title; channels that don't support a subject can ignore.
        body: full body content; the orchestrator already passed it through DLP.
        kind: declared channel kind (see :class:`ChannelKind`).
        metadata: additional headers/tags – channel implementations decide
            whether to forward them.
    """

    tenant_id: str
    recipient: str
    subject: str
    body: str
    kind: ChannelKind = ChannelKind.NULL
    run_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChannelDeliveryResult:
    """Result returned by :meth:`ChannelAdapter.send`."""

    channel: str
    delivered: bool
    detail: Mapping[str, Any] = field(default_factory=dict)
    transport_id: Optional[str] = None


class ChannelAdapter(ABC):
    """Abstract channel implementation.

    Subclasses must be safe to call from worker threads; deliveries are
    expected to be quick (<1s) – long-running transports must enqueue and
    return ``transport_id`` for later reconciliation.
    """

    name: str
    kind: ChannelKind

    @abstractmethod
    def send(self, message: OutboundMessage) -> ChannelDeliveryResult:
        """Deliver ``message``; raise on transport failure."""


class ChannelError(Exception):
    """Raised when an adapter declines or transport fails permanently."""


__all__ = [
    "ChannelAdapter",
    "ChannelDeliveryResult",
    "ChannelError",
    "ChannelKind",
    "OutboundMessage",
]
