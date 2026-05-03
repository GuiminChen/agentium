"""Message queue adapters."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List

from agentium.infra.mq.inproc_bus import BusMessage, InprocBus


@dataclass(frozen=True)
class Message:
    """Typed in-process message payload."""

    key: str
    payload: Dict[str, Any]


class InMemoryMessageQueue:
    """FIFO in-memory queue for local asynchronous boundaries."""

    def __init__(self) -> None:
        self._topics: Dict[str, Deque[Message]] = defaultdict(deque)

    def publish(self, topic: str, message: Message) -> None:
        """Publish one message to a topic."""

        self._topics[topic].append(message)

    def consume(self, topic: str, limit: int = 1) -> List[Message]:
        """Consume up to ``limit`` messages from a topic."""

        if limit <= 0:
            return []
        messages: List[Message] = []
        queue = self._topics[topic]
        while queue and len(messages) < limit:
            messages.append(queue.popleft())
        return messages


__all__ = ["BusMessage", "InMemoryMessageQueue", "InprocBus", "Message"]
