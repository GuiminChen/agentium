"""Task lock lease types (P2 / Anthropic #17)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskLockLease:
    """An acquired lease on a logical resource key."""

    tenant_id: str
    resource_key: str
    holder_run_id: str
    issued_at: float
    expires_at: float
