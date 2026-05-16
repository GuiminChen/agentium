"""Mem0-class memory backend adapter (experimental stub).

This module adapts an external Mem0-style memory client (e.g. the open source
`Mem0 <https://github.com/mem0ai/mem0>`_ project) so it can plug into the
:class:`~agentium.memory.memory_service.MemoryService` protocol. It is
intentionally implemented as a thin translation layer:

* No hard dependency on the ``mem0`` package. The adapter accepts an opaque
  ``mem0_client`` object satisfying the :class:`Mem0Client` protocol.
* Tenant isolation is enforced by :class:`MemoryService` at the call site;
  this adapter merely forwards the ``tenant_id`` field to the client so
  the upstream index can partition data.
* When no client is configured the adapter fails loud with ``RuntimeError``
  rather than silently returning empty slices, preserving the paper's
  "subordinate writer" contract for durable memory.

The adapter is flagged *experimental* and exists mainly to give the paper's
"Mem0-class backends" language a real integration point. Production code
paths should keep using the first-party in-memory or SQLite backends until a
specific Mem0 client is evaluated against the six acceptance gates.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional, Protocol, Sequence

from agentium.memory.types import MemoryLayer, MemoryRecord


class Mem0Client(Protocol):
    """Protocol satisfied by any Mem0-style client consumed by this adapter.

    Implementations must support tenant-scoped append, query, and purge. The
    adapter keeps the surface tiny so a thin wrapper around the real ``mem0``
    Python SDK, or a mock client for tests, can both satisfy it.
    """

    def add(
        self,
        *,
        tenant_id: str,
        layer: str,
        key: str,
        payload: dict,
        created_at: str,
    ) -> None: ...

    def search(
        self,
        *,
        tenant_id: str,
        layer: str,
        limit: int,
    ) -> Sequence[dict]: ...

    def delete(self, *, tenant_id: str) -> int: ...


class Mem0Backend:
    """Adapter that satisfies :class:`MemoryBackend` via a Mem0-style client.

    The adapter is intentionally minimal; advanced features (similarity
    search, embeddings config, consolidation hooks) belong in the external
    client rather than in Agentium's governed surface. For native backends,
    :mod:`agentium.coordination.chat_mid_semantic_memory` performs a
    Mem0-like LLM extraction pass into MID without embedding fusion.
    """

    _NOT_CONFIGURED = (
        "Mem0 client not configured: pass a Mem0Client implementation to "
        "Mem0Backend(client=...) or switch to InMemoryBackend / SqliteMemoryBackend."
    )

    def __init__(self, client: "Mem0Client | None" = None) -> None:
        self._client = client

    @property
    def is_configured(self) -> bool:
        """Return whether a Mem0 client is wired up for this adapter."""

        return self._client is not None

    def append(self, record: MemoryRecord) -> None:
        """Forward the record to the Mem0 client with tenant scoping."""

        client = self._require_client()
        client.add(
            tenant_id=record.tenant_id,
            layer=record.layer.value,
            key=record.key,
            payload=dict(record.payload),
            created_at=record.created_at.isoformat(),
        )

    def query(
        self,
        tenant_id: str,
        layer: MemoryLayer,
        limit: int = 50,
        *,
        run_id_filter: Optional[str] = None,
    ) -> List[MemoryRecord]:
        """Delegate to the Mem0 client; translate raw rows back to records."""

        client = self._require_client()
        bounded_limit = max(1, int(limit))
        fetch_cap = bounded_limit * 8 if run_id_filter else bounded_limit
        raw_rows = client.search(
            tenant_id=tenant_id,
            layer=layer.value,
            limit=max(bounded_limit, fetch_cap),
        )
        rows = [self._row_to_record(row, default_layer=layer) for row in raw_rows]
        if run_id_filter is not None and str(run_id_filter).strip():
            rid = str(run_id_filter).strip()
            rows = [r for r in rows if str(r.payload.get("run_id") or "").strip() == rid]
        return rows[-bounded_limit:]

    def purge_tenant(self, tenant_id: str) -> int:
        """Delete all Mem0-held records for ``tenant_id``."""

        client = self._require_client()
        removed = client.delete(tenant_id=tenant_id)
        return int(removed or 0)

    def _require_client(self) -> "Mem0Client":
        if self._client is None:
            raise RuntimeError(self._NOT_CONFIGURED)
        return self._client

    @staticmethod
    def _row_to_record(row: Any, *, default_layer: MemoryLayer) -> MemoryRecord:
        if not isinstance(row, dict):
            raise TypeError("Mem0 client returned a non-dict row; expected dict")
        tenant_id = str(row.get("tenant_id", "")).strip()
        if not tenant_id:
            raise ValueError("Mem0 row missing tenant_id")
        layer_value = row.get("layer")
        layer = MemoryLayer(layer_value) if layer_value else default_layer
        key = str(row.get("key", "")).strip() or "mem0"
        payload = row.get("payload", {})
        if not isinstance(payload, dict):
            raise TypeError("Mem0 row payload must be a dict")
        created_raw = row.get("created_at")
        if isinstance(created_raw, datetime):
            created_at = created_raw
        elif isinstance(created_raw, str) and created_raw:
            created_at = datetime.fromisoformat(created_raw)
        else:
            created_at = datetime.now(timezone.utc)
        return MemoryRecord(
            tenant_id=tenant_id,
            layer=layer,
            key=key,
            payload=dict(payload),
            created_at=created_at,
        )


__all__ = ["Mem0Backend", "Mem0Client"]
