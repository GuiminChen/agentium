"""Null channel adapter for tests and `dry-run` deployments.

The null adapter records every delivery in memory so callers can assert on
the rendered payload without touching the network.  It is the default
adapter wired by the bootstrap when no channel is configured, so the
backend is *always* able to call ``OutboundOrchestrator.send`` safely.
"""

from __future__ import annotations

import threading
from typing import List

from agentium.channels.base import (
    ChannelAdapter,
    ChannelDeliveryResult,
    ChannelKind,
    OutboundMessage,
)


class NullChannelAdapter(ChannelAdapter):
    """Channel adapter that captures messages instead of dispatching them."""

    name = "null"
    kind = ChannelKind.NULL

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sent: List[OutboundMessage] = []

    def send(self, message: OutboundMessage) -> ChannelDeliveryResult:
        with self._lock:
            self._sent.append(message)
        return ChannelDeliveryResult(
            channel=self.name,
            delivered=True,
            detail={"recorded_index": len(self._sent) - 1},
        )

    @property
    def sent(self) -> List[OutboundMessage]:
        """Return a defensive copy of all captured messages."""

        with self._lock:
            return list(self._sent)

    def reset(self) -> None:
        with self._lock:
            self._sent.clear()


__all__ = ["NullChannelAdapter"]
