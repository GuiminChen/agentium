"""Per-tenant resource quotas with deterministic accounting.

The :class:`ResourceManager` complements :class:`BudgetLedger` by tracking
*operational* resources (concurrent file handles, in-memory bytes, current
sandbox workers) instead of token / cost spend.  PRD §3.16 / §3.5 refer to it
as the docker ``cgroups`` analogue: hard limits with explicit reject, soft
limits trigger warnings and are surfaced via telemetry.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional


class ResourceQuotaExceededError(Exception):
    """Raised when a hard limit would be exceeded by a reservation."""


@dataclass(frozen=True)
class ResourceQuota:
    """Hard / soft caps for a single resource on a single tenant.

    Attributes:
        hard_limit: requests above this are rejected.
        soft_limit: requests above this still succeed but mark
            :attr:`ResourceUsage.soft_breached` for visibility.
    """

    hard_limit: int
    soft_limit: Optional[int] = None

    def __post_init__(self) -> None:
        if self.hard_limit <= 0:
            raise ValueError("hard_limit must be positive")
        if self.soft_limit is not None and self.soft_limit > self.hard_limit:
            raise ValueError("soft_limit cannot exceed hard_limit")


@dataclass
class ResourceUsage:
    """Snapshot of current consumption for telemetry."""

    tenant_id: str
    resource: str
    current: int
    hard_limit: int
    soft_limit: Optional[int]
    soft_breached: bool


@dataclass
class _Account:
    quotas: Dict[str, ResourceQuota] = field(default_factory=dict)
    usage: Dict[str, int] = field(default_factory=dict)


class ResourceManager:
    """Thread-safe quota ledger keyed by ``(tenant_id, resource)``.

    Args:
        defaults: optional per-resource default quota applied when a tenant has
            no specific entry.  ``None`` means the resource is unbounded for
            unconfigured tenants.
    """

    def __init__(
        self,
        defaults: Optional[Mapping[str, ResourceQuota]] = None,
    ) -> None:
        self._defaults: Dict[str, ResourceQuota] = dict(defaults or {})
        self._accounts: Dict[str, _Account] = {}
        self._lock = threading.RLock()

    def configure(
        self,
        tenant_id: str,
        resource: str,
        quota: ResourceQuota,
    ) -> None:
        """Set or replace a tenant-specific quota."""

        with self._lock:
            account = self._accounts.setdefault(tenant_id, _Account())
            account.quotas[resource] = quota

    def reserve(self, tenant_id: str, resource: str, amount: int = 1) -> ResourceUsage:
        """Reserve ``amount`` of ``resource`` for ``tenant_id``.

        Raises ``ResourceQuotaExceededError`` if the request would breach the
        hard limit.  Returns the new usage snapshot.
        """

        if amount <= 0:
            raise ValueError("amount must be positive")
        with self._lock:
            account = self._accounts.setdefault(tenant_id, _Account())
            quota = account.quotas.get(resource) or self._defaults.get(resource)
            current = account.usage.get(resource, 0)
            new_value = current + amount
            if quota is not None and new_value > quota.hard_limit:
                raise ResourceQuotaExceededError(
                    f"hard quota exceeded for {tenant_id}/{resource}: "
                    f"{new_value} > {quota.hard_limit}"
                )
            account.usage[resource] = new_value
            soft_breached = bool(
                quota is not None
                and quota.soft_limit is not None
                and new_value > quota.soft_limit
            )
            return ResourceUsage(
                tenant_id=tenant_id,
                resource=resource,
                current=new_value,
                hard_limit=quota.hard_limit if quota else 0,
                soft_limit=quota.soft_limit if quota else None,
                soft_breached=soft_breached,
            )

    def release(self, tenant_id: str, resource: str, amount: int = 1) -> None:
        """Release ``amount`` previously reserved.  Never goes below 0."""

        if amount <= 0:
            raise ValueError("amount must be positive")
        with self._lock:
            account = self._accounts.get(tenant_id)
            if account is None:
                return
            current = account.usage.get(resource, 0)
            account.usage[resource] = max(0, current - amount)

    def usage(self, tenant_id: str, resource: str) -> ResourceUsage:
        """Read-only snapshot for ``(tenant_id, resource)``."""

        with self._lock:
            account = self._accounts.get(tenant_id)
            current = account.usage.get(resource, 0) if account else 0
            quota = (
                (account.quotas.get(resource) if account else None)
                or self._defaults.get(resource)
            )
            soft_breached = bool(
                quota is not None
                and quota.soft_limit is not None
                and current > quota.soft_limit
            )
            return ResourceUsage(
                tenant_id=tenant_id,
                resource=resource,
                current=current,
                hard_limit=quota.hard_limit if quota else 0,
                soft_limit=quota.soft_limit if quota else None,
                soft_breached=soft_breached,
            )

    def lease(self, tenant_id: str, resource: str, amount: int = 1) -> "_ResourceLease":
        """Context manager that auto-releases on exit."""

        return _ResourceLease(self, tenant_id, resource, amount)


class _ResourceLease:
    """RAII helper used by :meth:`ResourceManager.lease`."""

    def __init__(
        self,
        manager: ResourceManager,
        tenant_id: str,
        resource: str,
        amount: int,
    ) -> None:
        self._manager = manager
        self._tenant_id = tenant_id
        self._resource = resource
        self._amount = amount
        self.usage: Optional[ResourceUsage] = None

    def __enter__(self) -> "_ResourceLease":
        self.usage = self._manager.reserve(
            self._tenant_id, self._resource, self._amount
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._manager.release(self._tenant_id, self._resource, self._amount)
