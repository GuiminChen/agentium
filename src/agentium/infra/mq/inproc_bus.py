"""In-process publish/subscribe bus used as the IPC fallback.

PRD §3.5 calls for a pluggable ``IPC_Bus`` (Redis Streams / RocketMQ / Kafka).
For the reference backend we ship a thread-safe in-memory implementation that
fulfills the same contract: producers ``publish(topic, message)`` synchronously,
subscribers receive messages through registered callbacks, and a
``replay(topic, since)`` helper supports crash-recovery integration tests.

The bus is intentionally minimal:
- Topics are plain strings; subscribers register a callable.
- Each topic maintains a bounded ring buffer for short-window replay.
- Delivery happens synchronously inside :meth:`publish` so unit tests don't
  need to coordinate threads.  A future Redis backend should preserve this
  semantics by writing-through before returning.
- Subscriber failures are isolated: they are captured and reported via the
  optional ``on_error`` hook.  The bus never crashes the publisher.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Iterable, List, Mapping, Optional


SubscriberFn = Callable[["BusMessage"], None]
ErrorHookFn = Callable[[str, "BusMessage", BaseException], None]


@dataclass
class BusMessage:
    """Envelope produced by :meth:`InprocBus.publish`."""

    id: str
    topic: str
    payload: Mapping[str, Any]
    published_at: float
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass
class _TopicState:
    subscribers: List[SubscriberFn] = field(default_factory=list)
    history: Deque[BusMessage] = field(default_factory=deque)


class InprocBus:
    """Thread-safe in-process pub/sub bus with bounded replay.

    Args:
        history_size: per-topic ring buffer length. ``0`` disables replay.
        clock: time source (for tests).
        on_error: optional hook ``(topic, message, exc)`` invoked when a
            subscriber raises.  Defaults to silent swallowing so a bad
            subscriber cannot block the producer.
    """

    def __init__(
        self,
        *,
        history_size: int = 256,
        clock: Callable[[], float] = time.time,
        on_error: Optional[ErrorHookFn] = None,
    ) -> None:
        if history_size < 0:
            raise ValueError("history_size must be >= 0")
        self._history_size = history_size
        self._clock = clock
        self._on_error = on_error
        self._topics: Dict[str, _TopicState] = {}
        self._lock = threading.RLock()
        self._counters: Dict[str, int] = {
            "published_total": 0,
            "delivered_total": 0,
            "subscriber_errors_total": 0,
        }

    def subscribe(self, topic: str, callback: SubscriberFn) -> Callable[[], None]:
        """Register ``callback`` for ``topic``; returns an unsubscribe function."""

        if not topic:
            raise ValueError("topic must not be empty")
        with self._lock:
            state = self._topics.setdefault(topic, _TopicState())
            state.subscribers.append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                state = self._topics.get(topic)
                if state is None:
                    return
                try:
                    state.subscribers.remove(callback)
                except ValueError:
                    pass

        return _unsubscribe

    def publish(
        self,
        topic: str,
        payload: Mapping[str, Any],
        *,
        headers: Optional[Mapping[str, str]] = None,
    ) -> BusMessage:
        """Deliver ``payload`` to all subscribers of ``topic`` synchronously."""

        if not topic:
            raise ValueError("topic must not be empty")
        message = BusMessage(
            id=uuid.uuid4().hex,
            topic=topic,
            payload=dict(payload),
            published_at=self._clock(),
            headers=dict(headers or {}),
        )
        with self._lock:
            state = self._topics.setdefault(topic, _TopicState())
            if self._history_size:
                state.history.append(message)
                while len(state.history) > self._history_size:
                    state.history.popleft()
            subscribers = list(state.subscribers)
            self._counters["published_total"] += 1

        for subscriber in subscribers:
            try:
                subscriber(message)
                with self._lock:
                    self._counters["delivered_total"] += 1
            except BaseException as exc:  # noqa: BLE001
                with self._lock:
                    self._counters["subscriber_errors_total"] += 1
                if self._on_error is not None:
                    try:
                        self._on_error(topic, message, exc)
                    except Exception:
                        pass
        return message

    def replay(
        self, topic: str, *, since: Optional[float] = None
    ) -> Iterable[BusMessage]:
        """Yield messages from the topic ring buffer published after ``since``."""

        with self._lock:
            state = self._topics.get(topic)
            if state is None:
                return []
            history = list(state.history)
        if since is None:
            return history
        return [m for m in history if m.published_at > since]

    def counters(self) -> Mapping[str, int]:
        with self._lock:
            return dict(self._counters)
