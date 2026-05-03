"""Lease manager for budget reservations and tool execution slots."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional


@dataclass
class Lease:
    """Lease record for a single holder.

    Attributes:
        lease_id: Stable identifier for the lease.
        holder: Logical holder identifier (e.g. run_id or tool_use_id).
        tenant_id: Tenant that owns the lease.
        expires_at: Wallclock UTC expiry timestamp.
        renew_count: Number of times the lease has been renewed.
    """

    lease_id: str
    holder: str
    tenant_id: str
    expires_at: datetime
    renew_count: int = 0


class LeaseManager:
    """Thread-safe lease manager with cooperative expiration sweep.

    Use cases:
    - Budget reservations that must be released on timeout to avoid deadlock.
    - Tool execution slots tied to a tenant fairness queue.

    The manager does NOT spawn its own thread. Callers periodically invoke
    ``sweep_expired()`` (e.g. from the background daemon or per-turn hook) to release stale
    leases. This keeps the implementation deterministic and testable.
    """

    def __init__(
        self,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._leases: Dict[str, Lease] = {}
        self._lock = threading.Lock()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def acquire(
        self,
        lease_id: str,
        holder: str,
        tenant_id: str,
        ttl_seconds: float,
    ) -> Lease:
        """Acquire a fresh lease. Replaces an existing lease with the same id."""

        if ttl_seconds <= 0:
            raise ValueError("lease ttl_seconds must be positive")
        expires_at = self._clock() + timedelta(seconds=ttl_seconds)
        lease = Lease(
            lease_id=lease_id,
            holder=holder,
            tenant_id=tenant_id,
            expires_at=expires_at,
        )
        with self._lock:
            self._leases[lease_id] = lease
        return lease

    def renew(self, lease_id: str, ttl_seconds: float) -> Optional[Lease]:
        """Extend an existing lease. Returns None if the lease is gone."""

        if ttl_seconds <= 0:
            raise ValueError("lease ttl_seconds must be positive")
        with self._lock:
            lease = self._leases.get(lease_id)
            if lease is None:
                return None
            lease.expires_at = self._clock() + timedelta(seconds=ttl_seconds)
            lease.renew_count += 1
            return lease

    def release(self, lease_id: str) -> bool:
        """Release a lease. Returns True if a lease was removed."""

        with self._lock:
            return self._leases.pop(lease_id, None) is not None

    def get(self, lease_id: str) -> Optional[Lease]:
        """Return a lease snapshot or None when missing."""

        with self._lock:
            return self._leases.get(lease_id)

    def sweep_expired(self) -> List[Lease]:
        """Remove and return all leases whose ``expires_at`` is in the past."""

        now = self._clock()
        expired: List[Lease] = []
        with self._lock:
            for lease_id, lease in list(self._leases.items()):
                if lease.expires_at <= now:
                    expired.append(lease)
                    del self._leases[lease_id]
        return expired

    def active_count(self) -> int:
        """Return current number of active leases (for telemetry)."""

        with self._lock:
            return len(self._leases)
