"""Layered memory service with strict tenant isolation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from typing_extensions import Protocol

from agentium.governance.audit_lineage import AuditSink
from agentium.memory.types import MemoryLayer, MemoryRecord
from agentium.models.context import AuditRecord, RequestContext
from agentium.shared.errors import PolicyDeniedError


class MemoryBackend(Protocol):
    """Protocol implemented by all memory backends."""

    def append(self, record: MemoryRecord) -> None: ...
    def query(
        self,
        tenant_id: str,
        layer: MemoryLayer,
        limit: int = 50,
        *,
        run_id_filter: Optional[str] = None,
    ) -> List[MemoryRecord]: ...
    def purge_tenant(self, tenant_id: str) -> int: ...


class MemoryService:
    """Thin facade over a backend that enforces tenant isolation invariants.

    Cross-tenant reads or writes are rejected with PolicyDeniedError plus
    an audit event ``memory_cross_tenant_blocked``. This guarantees that
    privilege escalation through memory is impossible regardless of the
    backend in use.
    """

    def __init__(
        self,
        backend: MemoryBackend,
        audit_sink: Optional[AuditSink] = None,
    ) -> None:
        self._backend = backend
        self._audit_sink = audit_sink

    def remember(
        self,
        context: RequestContext,
        layer: MemoryLayer,
        key: str,
        payload: Dict[str, Any],
        target_tenant_id: Optional[str] = None,
    ) -> MemoryRecord:
        """Persist one memory record under the request's tenant scope."""

        effective_tenant = target_tenant_id or context.tenant_id
        self._assert_same_tenant(context, effective_tenant, action="write")
        record = MemoryRecord(
            tenant_id=effective_tenant,
            layer=layer,
            key=key,
            payload=payload,
        )
        self._backend.append(record)
        return record

    def recall(
        self,
        context: RequestContext,
        layer: MemoryLayer,
        target_tenant_id: Optional[str] = None,
        limit: int = 50,
        *,
        run_id_filter: Optional[str] = None,
    ) -> List[MemoryRecord]:
        """Read records for one tenant + layer with a hard tenant check."""

        effective_tenant = target_tenant_id or context.tenant_id
        self._assert_same_tenant(context, effective_tenant, action="read")
        return self._backend.query(
            tenant_id=effective_tenant,
            layer=layer,
            limit=limit,
            run_id_filter=run_id_filter,
        )

    def purge(self, context: RequestContext, target_tenant_id: Optional[str] = None) -> int:
        """Purge memory for the caller's tenant (or explicit target)."""

        effective_tenant = target_tenant_id or context.tenant_id
        self._assert_same_tenant(context, effective_tenant, action="purge")
        return self._backend.purge_tenant(effective_tenant)

    def _assert_same_tenant(
        self, context: RequestContext, target_tenant_id: str, action: str
    ) -> None:
        if not target_tenant_id or target_tenant_id != context.tenant_id:
            self._audit_cross_tenant(context, target_tenant_id, action)
            raise PolicyDeniedError("memory cross-tenant access blocked")

    def _audit_cross_tenant(
        self,
        context: RequestContext,
        target_tenant_id: str,
        action: str,
    ) -> None:
        if self._audit_sink is None:
            return
        try:
            self._audit_sink.append(
                AuditRecord(
                    event_type="memory_cross_tenant_blocked",
                    tenant_id=context.tenant_id,
                    run_id=context.run_id,
                    payload={
                        "target_tenant_id": target_tenant_id,
                        "action": action,
                    },
                )
            )
        except Exception:
            pass
