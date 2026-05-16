"""Ingress coordination exceptions visible to HTTP handlers."""

from __future__ import annotations

from typing import Literal, Optional


class ChatIngressDeferred(Exception):
    """Raised when the message is accepted but execution is deferred (queue semantics)."""

    def __init__(
        self,
        kind: Literal["followup", "steer", "collect"],
        *,
        queue_depth: int = 0,
        collect_flush_after_ms: Optional[int] = None,
    ) -> None:
        super().__init__(kind)
        self.kind = kind
        self.queue_depth = queue_depth
        self.collect_flush_after_ms = collect_flush_after_ms
