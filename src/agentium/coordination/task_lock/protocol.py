"""Task lock backend protocol."""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from agentium.coordination.task_lock.types import TaskLockLease


@runtime_checkable
class TaskLockBackend(Protocol):
    """Cross-worker mutex on ``resource_key`` scoped by ``tenant_id``."""

    def try_acquire(
        self,
        *,
        tenant_id: str,
        resource_key: str,
        holder_run_id: str,
        ttl_seconds: float,
        metadata_json: Optional[str] = None,
    ) -> Optional[TaskLockLease]:
        """Return a new lease if acquired; ``None`` if denied (or backend disabled).

        A denied acquire must not mutate an existing lease held by another holder.
        """

    def renew(
        self,
        *,
        tenant_id: str,
        resource_key: str,
        holder_run_id: str,
        ttl_seconds: float,
    ) -> Optional[TaskLockLease]:
        """Extend TTL if ``holder_run_id`` matches the active lease."""

    def release(self, *, tenant_id: str, resource_key: str, holder_run_id: str) -> bool:
        """Remove the lease if the holder matches; return whether a row was deleted."""
