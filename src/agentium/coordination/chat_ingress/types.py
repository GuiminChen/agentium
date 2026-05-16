"""Typed payloads for follow-up queue serialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

FollowupPayload = Dict[str, Any]


def session_ingress_key(tenant_id: str, session_id: str) -> str:
    """Stable key for backends (colon-safe when tenant/session are validated upstream)."""

    return f"{tenant_id.strip()}:{session_id.strip()}"


@dataclass(frozen=True)
class CollectAppendResult:
    """Outcome of appending one fragment to the collect buffer."""

    defer_http: bool
    fragment_depth: int
    flush_after_ms: Optional[int]
    merged_immediate_text: Optional[str] = None
