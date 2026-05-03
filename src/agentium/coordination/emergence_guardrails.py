"""Cross-run guardrails that prevent emergent overshoot/oscillation/divergence.

PRD §3.2 / docker-design §healthcheck call this the *EmergenceGuardrails*: a
small ledger that watches global counters (outbound calls, fan-out depth,
cost) across coordinated agents and trips a circuit breaker before the
system spirals out of control.

The implementation is intentionally process-local; clustered deployments
should back this with the IPC bus or a centralised counter store.  The API is
stable so the storage swap can happen later without changing call sites.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Mapping, Optional, Tuple


class GuardrailState(str, Enum):
    """Tri-state circuit-breaker style status."""

    OK = "ok"
    WARN = "warn"
    TRIPPED = "tripped"


@dataclass(frozen=True)
class GuardrailLimit:
    """Per-counter ceiling.

    Attributes:
        warn_threshold: emit a warning when usage >= warn_threshold.
        hard_limit: reject increments that would push usage above this.
        window_seconds: optional sliding window; ``None`` means cumulative.
    """

    warn_threshold: int
    hard_limit: int
    window_seconds: Optional[float] = None

    def __post_init__(self) -> None:
        if self.warn_threshold < 0 or self.hard_limit <= 0:
            raise ValueError("thresholds must be positive")
        if self.warn_threshold > self.hard_limit:
            raise ValueError("warn_threshold cannot exceed hard_limit")
        if self.window_seconds is not None and self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive when set")


@dataclass
class GuardrailDecision:
    """Outcome returned from :meth:`EmergenceGuardrails.try_increment`."""

    state: GuardrailState
    counter: str
    scope: Tuple[str, str]
    current: int
    warn_threshold: int
    hard_limit: int
    reason: str = ""


@dataclass
class _CounterState:
    total: int = 0
    events: list = field(default_factory=list)


class EmergenceGuardrails:
    """Circuit-breaker for cross-run/cross-tenant emergent behaviour.

    Args:
        limits: mapping of counter name → :class:`GuardrailLimit`.
        clock: monotonic clock; replaceable for tests.
        on_trip: optional callback invoked when a counter trips for the first
            time in the current window.  Use this to publish to ``InprocBus``
            or trigger background-plane quiesce.
    """

    def __init__(
        self,
        limits: Mapping[str, GuardrailLimit],
        *,
        clock: Callable[[], float] = time.time,
        on_trip: Optional[Callable[[GuardrailDecision], None]] = None,
    ) -> None:
        self._limits = dict(limits)
        self._clock = clock
        self._on_trip = on_trip
        self._states: Dict[Tuple[str, str, str], _CounterState] = {}
        self._tripped: Dict[Tuple[str, str, str], bool] = {}
        self._lock = threading.RLock()

    def _key(self, counter: str, tenant_id: str, scope_id: str) -> Tuple[str, str, str]:
        return (counter, tenant_id, scope_id)

    def configure(self, counter: str, limit: GuardrailLimit) -> None:
        with self._lock:
            self._limits[counter] = limit

    def try_increment(
        self,
        counter: str,
        tenant_id: str,
        scope_id: str = "global",
        amount: int = 1,
    ) -> GuardrailDecision:
        """Attempt to add ``amount`` to ``counter``.

        Returns a :class:`GuardrailDecision`; ``state == TRIPPED`` means the
        increment was rejected and the caller must back off.
        """

        if amount <= 0:
            raise ValueError("amount must be positive")
        limit = self._limits.get(counter)
        if limit is None:
            return GuardrailDecision(
                state=GuardrailState.OK,
                counter=counter,
                scope=(tenant_id, scope_id),
                current=0,
                warn_threshold=0,
                hard_limit=0,
            )
        key = self._key(counter, tenant_id, scope_id)
        now = self._clock()
        with self._lock:
            state = self._states.setdefault(key, _CounterState())
            if limit.window_seconds is not None:
                cutoff = now - limit.window_seconds
                state.events = [(t, n) for (t, n) in state.events if t > cutoff]
                state.total = sum(n for _, n in state.events)
            new_total = state.total + amount
            if new_total > limit.hard_limit:
                if not self._tripped.get(key):
                    self._tripped[key] = True
                    decision = GuardrailDecision(
                        state=GuardrailState.TRIPPED,
                        counter=counter,
                        scope=(tenant_id, scope_id),
                        current=state.total,
                        warn_threshold=limit.warn_threshold,
                        hard_limit=limit.hard_limit,
                        reason=f"hard_limit {limit.hard_limit} reached",
                    )
                    self._maybe_notify(decision)
                    return decision
                return GuardrailDecision(
                    state=GuardrailState.TRIPPED,
                    counter=counter,
                    scope=(tenant_id, scope_id),
                    current=state.total,
                    warn_threshold=limit.warn_threshold,
                    hard_limit=limit.hard_limit,
                    reason="already tripped",
                )
            state.total = new_total
            if limit.window_seconds is not None:
                state.events.append((now, amount))
            self._tripped[key] = False
            if state.total >= limit.warn_threshold:
                return GuardrailDecision(
                    state=GuardrailState.WARN,
                    counter=counter,
                    scope=(tenant_id, scope_id),
                    current=state.total,
                    warn_threshold=limit.warn_threshold,
                    hard_limit=limit.hard_limit,
                    reason="warn_threshold reached",
                )
            return GuardrailDecision(
                state=GuardrailState.OK,
                counter=counter,
                scope=(tenant_id, scope_id),
                current=state.total,
                warn_threshold=limit.warn_threshold,
                hard_limit=limit.hard_limit,
            )

    def reset(
        self, counter: Optional[str] = None, tenant_id: Optional[str] = None
    ) -> None:
        """Clear counters; useful between e2e suites."""

        with self._lock:
            if counter is None and tenant_id is None:
                self._states.clear()
                self._tripped.clear()
                return
            keys = list(self._states.keys())
            for key in keys:
                if counter is not None and key[0] != counter:
                    continue
                if tenant_id is not None and key[1] != tenant_id:
                    continue
                self._states.pop(key, None)
                self._tripped.pop(key, None)

    def _maybe_notify(self, decision: GuardrailDecision) -> None:
        if self._on_trip is None:
            return
        try:
            self._on_trip(decision)
        except Exception:
            pass
