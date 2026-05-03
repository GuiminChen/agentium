"""Cooperative run cancellation flags (HTTP + workflow node boundaries)."""

from __future__ import annotations

from threading import Lock
from typing import Set


class RunCancelRegistry:
    """Thread-safe set of run_id values marked cancelled by operators."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._cancelled: Set[str] = set()

    def cancel(self, run_id: str) -> None:
        with self._lock:
            self._cancelled.add(run_id)

    def is_cancelled(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._cancelled

    def clear(self, run_id: str) -> None:
        """Test helper: remove cancellation marker."""

        with self._lock:
            self._cancelled.discard(run_id)


__all__ = ["RunCancelRegistry"]
