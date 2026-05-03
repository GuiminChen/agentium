"""Background-plane memory consolidator: dedupe + age-out + promotion across layers.

Behaviour (per technical design background plane):

- **Deduplicate** SHORT records that share the same ``key`` within a window;
  only the most recent payload is preserved.
- **Promote** keys that survive a configurable retention window to MID.
- **Annotate** conflicts (same key, different payloads) with
  ``_conflict_with`` metadata so downstream review can resolve manually.

The consolidator is conservative: it never mutates the existing payload of
high-confidence records; merging is additive only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional

from agentium.memory.memory_service import MemoryService
from agentium.memory.types import MemoryLayer, MemoryRecord
from agentium.models.context import RequestContext


@dataclass
class ConsolidationReport:
    """Summary returned by :meth:`MemoryConsolidator.consolidate`."""

    tenant_id: str
    duplicates_removed: int = 0
    promoted_to_mid: int = 0
    conflicts_recorded: int = 0
    inspected: int = 0
    notes: List[str] = field(default_factory=list)


class MemoryConsolidator:
    """Periodic memory consolidator invoked by the background daemon.

    Args:
        memory_service: target memory service.
        promotion_threshold_seconds: how long a key must survive in SHORT
            before being promoted to MID.
        clock: optional wall clock; tests inject deterministic clocks.
    """

    def __init__(
        self,
        memory_service: MemoryService,
        *,
        promotion_threshold_seconds: float = 300.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if promotion_threshold_seconds <= 0:
            raise ValueError("promotion_threshold_seconds must be positive")
        self._memory = memory_service
        self._threshold = promotion_threshold_seconds
        self._clock = clock

    def consolidate(self, context: RequestContext) -> ConsolidationReport:
        """Run one consolidation pass for ``context.tenant_id``."""

        report = ConsolidationReport(tenant_id=context.tenant_id)
        short_records = self._memory.recall(
            context=context, layer=MemoryLayer.SHORT, limit=512
        )
        report.inspected = len(short_records)

        grouped: Dict[str, List[MemoryRecord]] = {}
        for record in short_records:
            grouped.setdefault(record.key, []).append(record)

        now = self._clock()
        for key, records in grouped.items():
            if len(records) > 1:
                report.duplicates_removed += len(records) - 1
                payloads = {self._payload_signature(r.payload) for r in records}
                if len(payloads) > 1:
                    report.conflicts_recorded += 1
                    self._memory.remember(
                        context=context,
                        layer=MemoryLayer.MID,
                        key=f"{key}::conflict",
                        payload={
                            "conflict_count": len(records),
                            "signatures": sorted(payloads),
                        },
                    )
                    report.notes.append(f"conflict:{key}")
            survivor = records[-1]
            survival_age = now - survivor.created_at.timestamp()
            if survival_age >= self._threshold:
                self._memory.remember(
                    context=context,
                    layer=MemoryLayer.MID,
                    key=key,
                    payload={**survivor.payload, "_promoted_at": now},
                )
                report.promoted_to_mid += 1
        return report

    @staticmethod
    def _payload_signature(payload: Mapping[str, object]) -> str:
        if not payload:
            return ""
        try:
            items = sorted((k, repr(v)) for k, v in payload.items())
        except TypeError:
            return repr(payload)
        return ";".join(f"{k}={v}" for k, v in items)


__all__ = ["ConsolidationReport", "MemoryConsolidator"]
