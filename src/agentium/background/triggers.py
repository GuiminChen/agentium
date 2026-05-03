"""Trigger primitives evaluated by the background daemon."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional


@dataclass(frozen=True)
class TriggerEvent:
    """One trigger firing event with attribution."""

    name: str
    fired_at: datetime
    payload: dict


@dataclass
class IntervalTrigger:
    """Fire periodically at a fixed cadence."""

    name: str
    interval_seconds: float
    last_fired_at: Optional[datetime] = None

    def should_fire(self, now: datetime) -> bool:
        if self.last_fired_at is None:
            return True
        elapsed = (now - self.last_fired_at).total_seconds()
        return elapsed >= self.interval_seconds

    def mark_fired(self, now: datetime) -> TriggerEvent:
        self.last_fired_at = now
        return TriggerEvent(name=self.name, fired_at=now, payload={})


@dataclass
class CallbackTrigger:
    """Fire whenever the predicate returns True."""

    name: str
    predicate: Callable[[], bool]

    def maybe_fire(self, now: datetime) -> Optional[TriggerEvent]:
        try:
            if self.predicate():
                return TriggerEvent(name=self.name, fired_at=now, payload={})
        except Exception:
            return None
        return None


def utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""

    return datetime.now(timezone.utc)
