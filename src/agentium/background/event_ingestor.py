"""Event ingestor: bridges external/runtime signals into background triggers.

Many proactive actions are not on a fixed schedule – they react to runtime
events (failed approvals, repeated rate limits, fresh memory items, channel
errors).  The ingestor receives these signals via :class:`InprocBus` and
exposes them to :class:`TriggerPlanner` as a deduplicated, time-bounded
queue.

The implementation is intentionally minimal:

- thread-safe FIFO with a configurable cap;
- deduplication by ``(topic, dedupe_key)`` within a TTL window;
- audit-friendly snapshot for tests and operator inspection.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Mapping, Optional


@dataclass(frozen=True)
class IngestedEvent:
    """One event consumed by the background plane."""

    topic: str
    payload: Mapping[str, object]
    received_at: float
    dedupe_key: Optional[str] = None
    headers: Mapping[str, str] = field(default_factory=dict)


class EventIngestor:
    """Bounded event queue with TTL-based deduplication.

    Args:
        max_events: maximum buffered events; oldest are dropped.
        dedupe_ttl_seconds: events with the same ``(topic, dedupe_key)`` are
            ignored if seen within this window.
        clock: optional wall clock for tests.
        rate_window_seconds: sliding window for :meth:`submissions_in_window`.
    """

    def __init__(
        self,
        *,
        max_events: int = 1024,
        dedupe_ttl_seconds: float = 60.0,
        clock: Callable[[], float] = time.time,
        rate_window_seconds: float = 1.0,
    ) -> None:
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        if dedupe_ttl_seconds < 0:
            raise ValueError("dedupe_ttl_seconds must be non-negative")
        self._max = max_events
        self._ttl = dedupe_ttl_seconds
        self._clock = clock
        self._rate_window = max(0.01, float(rate_window_seconds))
        self._queue: Deque[IngestedEvent] = deque(maxlen=max_events)
        self._dedupe: Dict[tuple[str, str], float] = {}
        self._rate_samples: Deque[float] = deque()
        self._lock = threading.Lock()

    def submit(
        self,
        topic: str,
        payload: Mapping[str, object],
        *,
        dedupe_key: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Optional[IngestedEvent]:
        """Queue an event; return the stored event or ``None`` if deduped."""

        if not topic:
            raise ValueError("topic must not be empty")
        now = self._clock()
        with self._lock:
            self._gc_dedupe(now)
            if dedupe_key is not None:
                key = (topic, dedupe_key)
                if key in self._dedupe:
                    return None
                self._dedupe[key] = now
            event = IngestedEvent(
                topic=topic,
                payload=dict(payload),
                received_at=now,
                dedupe_key=dedupe_key,
                headers=dict(headers or {}),
            )
            self._queue.append(event)
            self._rate_samples.append(now)
            self._trim_rate_samples(now)
            return event

    def submissions_in_window(self) -> int:
        """Approximate ingest rate: submissions recorded in the last rate window."""

        now = self._clock()
        with self._lock:
            self._trim_rate_samples(now)
            return len(self._rate_samples)

    def _trim_rate_samples(self, now: float) -> None:
        cutoff = now - self._rate_window
        while self._rate_samples and self._rate_samples[0] < cutoff:
            self._rate_samples.popleft()

    def drain(self, max_events: Optional[int] = None) -> List[IngestedEvent]:
        """Return up to ``max_events`` events in FIFO order, removing them."""

        with self._lock:
            limit = (
                len(self._queue) if max_events is None else min(max_events, len(self._queue))
            )
            events = [self._queue.popleft() for _ in range(limit)]
            return events

    def peek(self) -> List[IngestedEvent]:
        """Return a defensive copy without consuming events (operator helper)."""

        with self._lock:
            return list(self._queue)

    def __len__(self) -> int:
        with self._lock:
            return len(self._queue)

    def _gc_dedupe(self, now: float) -> None:
        if self._ttl <= 0:
            return
        cutoff = now - self._ttl
        stale = [k for k, ts in self._dedupe.items() if ts <= cutoff]
        for key in stale:
            self._dedupe.pop(key, None)


__all__ = ["EventIngestor", "IngestedEvent"]
