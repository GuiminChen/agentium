"""Channel adapters and outbound orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from agentium.channels.base import (
    ChannelAdapter,
    ChannelDeliveryResult,
    ChannelError,
    ChannelKind,
    OutboundMessage,
)
from agentium.channels.email_adapter import EmailChannelAdapter
from agentium.channels.null_adapter import NullChannelAdapter
from agentium.channels.outbound_orchestrator import (
    OutboundDispatch,
    OutboundOrchestrator,
    QuietHours,
    RateLimit,
)
from agentium.channels.web_adapter import WebChannelAdapter


@dataclass(frozen=True)
class ChannelEnvelope:
    """Message accepted by a simple channel adapter."""

    channel_name: str
    run_id: str
    payload: Dict[str, Any]
    async_delivery: bool = False


@dataclass(frozen=True)
class ChannelReceipt:
    """Delivery receipt returned by a simple channel adapter."""

    accepted: bool
    delivery_mode: str


class InMemoryChannelAdapter:
    """Minimal channel adapter used for local tests and synchronous runtimes."""

    def __init__(self, channel_name: str) -> None:
        self._channel_name = channel_name
        self._queue: List[ChannelEnvelope] = []

    def send(self, envelope: ChannelEnvelope) -> ChannelReceipt:
        """Accept one envelope and record whether it is sync or async."""

        if envelope.channel_name != self._channel_name:
            return ChannelReceipt(accepted=False, delivery_mode="rejected")
        mode = "async" if envelope.async_delivery else "sync"
        self._queue.append(envelope)
        return ChannelReceipt(accepted=True, delivery_mode=mode)

    def drain(self) -> List[ChannelEnvelope]:
        """Drain queued envelopes in FIFO order."""

        envelopes = list(self._queue)
        self._queue.clear()
        return envelopes


__all__ = [
    "ChannelAdapter",
    "ChannelDeliveryResult",
    "ChannelEnvelope",
    "ChannelError",
    "ChannelKind",
    "ChannelReceipt",
    "EmailChannelAdapter",
    "InMemoryChannelAdapter",
    "NullChannelAdapter",
    "OutboundDispatch",
    "OutboundMessage",
    "OutboundOrchestrator",
    "QuietHours",
    "RateLimit",
    "WebChannelAdapter",
]
