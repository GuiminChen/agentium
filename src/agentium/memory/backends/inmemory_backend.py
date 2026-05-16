"""In-memory backend for the layered MemoryService."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, List, Optional, Tuple

from agentium.memory.types import MemoryLayer, MemoryRecord


class InMemoryBackend:
    """Thread-safe in-memory backend keyed by tenant + layer."""

    def __init__(self) -> None:
        self._records: Dict[Tuple[str, MemoryLayer], List[MemoryRecord]] = defaultdict(list)
        self._lock = Lock()

    def append(self, record: MemoryRecord) -> None:
        with self._lock:
            key = (record.tenant_id, record.layer)
            self._records[key].append(record)

    def query(
        self,
        tenant_id: str,
        layer: MemoryLayer,
        limit: int = 50,
        *,
        run_id_filter: Optional[str] = None,
    ) -> List[MemoryRecord]:
        with self._lock:
            key = (tenant_id, layer)
            records = list(self._records.get(key, []))
        if run_id_filter is not None and str(run_id_filter).strip():
            rid = str(run_id_filter).strip()
            records = [r for r in records if str(r.payload.get("run_id") or "").strip() == rid]
        cap = max(1, int(limit))
        return records[-cap:]

    def purge_tenant(self, tenant_id: str) -> int:
        removed = 0
        with self._lock:
            for key in list(self._records.keys()):
                if key[0] == tenant_id:
                    removed += len(self._records[key])
                    del self._records[key]
        return removed
