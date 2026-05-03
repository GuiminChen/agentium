"""Cooperative cancellation primitives for runtime and orchestrator paths."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional


@dataclass
class CancelReason:
    """Reason associated with one cancellation event."""

    source: str
    detail: Optional[str] = None


class CancelToken:
    """Cooperative cancellation token.

    Holders SHOULD periodically call ``raise_if_cancelled()`` between work
    units. Cancellation is one-way and idempotent.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason: Optional[CancelReason] = None
        self._lock = threading.Lock()

    def cancel(self, source: str, detail: Optional[str] = None) -> None:
        """Mark token as cancelled with attribution."""

        with self._lock:
            if self._event.is_set():
                return
            self._reason = CancelReason(source=source, detail=detail)
            self._event.set()

    @property
    def cancelled(self) -> bool:
        """Return True if cancellation has been requested."""

        return self._event.is_set()

    @property
    def reason(self) -> Optional[CancelReason]:
        """Return cancellation attribution or None when not cancelled."""

        return self._reason

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until cancelled or until ``timeout`` expires."""

        return self._event.wait(timeout=timeout)

    def raise_if_cancelled(self) -> None:
        """Raise CancelledError when cancellation has been requested."""

        if self._event.is_set():
            raise CancelledError(self._reason)


class CancelledError(RuntimeError):
    """Raised to surface cooperative cancellation to callers."""

    def __init__(self, reason: Optional[CancelReason]) -> None:
        message = "cancelled"
        if reason is not None:
            message = f"cancelled by {reason.source}"
            if reason.detail:
                message = f"{message}: {reason.detail}"
        super().__init__(message)
        self.reason = reason
