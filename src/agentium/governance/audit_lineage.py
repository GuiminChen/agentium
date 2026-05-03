"""Audit lineage components for append-only runtime evidence."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import List, Optional

from typing_extensions import Protocol

from agentium.models.context import AuditRecord


class AuditSink(Protocol):
    """Protocol for audit persistence backends."""

    def append(self, record: AuditRecord) -> None:
        """Append one audit record to backend storage."""

    def query(
        self, run_id: Optional[str] = None, tenant_id: Optional[str] = None
    ) -> List[AuditRecord]:
        """Query audit records by optional filters."""


class InMemoryAuditSink:
    """In-memory audit storage for local testing and early development."""

    def __init__(self) -> None:
        self._records: List[AuditRecord] = []

    def append(self, record: AuditRecord) -> None:
        """Append one immutable audit record."""

        self._records.append(record.copy(deep=True))

    def query(
        self, run_id: Optional[str] = None, tenant_id: Optional[str] = None
    ) -> List[AuditRecord]:
        """Return records matching optional filters.

        Args:
            run_id: Optional runtime identifier filter.
            tenant_id: Optional tenant identifier filter.
        """

        matched: List[AuditRecord] = []
        for record in self._records:
            if run_id is not None and record.run_id != run_id:
                continue
            if tenant_id is not None and record.tenant_id != tenant_id:
                continue
            matched.append(record.copy(deep=True))
        return matched

    def clear(self) -> None:
        """Clear all in-memory records.

        This helper is intended for tests only.
        """

        self._records = []


class JsonlAuditSink:
    """Append-only JSONL audit sink with simple file persistence."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.touch()
        self._lock = Lock()

    def append(self, record: AuditRecord) -> None:
        """Append one audit record as JSON line."""

        if hasattr(record, "model_dump_json"):
            line = record.model_dump_json()
        else:
            line = record.json()
        with self._lock:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def query(
        self, run_id: Optional[str] = None, tenant_id: Optional[str] = None
    ) -> List[AuditRecord]:
        """Query records from JSONL file by optional filters."""

        matched: List[AuditRecord] = []
        with self._lock:
            with self._path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    raw = line.strip()
                    if not raw:
                        continue
                    if hasattr(AuditRecord, "model_validate"):
                        record = AuditRecord.model_validate(json.loads(raw))
                    else:
                        record = AuditRecord.parse_obj(json.loads(raw))
                    if run_id is not None and record.run_id != run_id:
                        continue
                    if tenant_id is not None and record.tenant_id != tenant_id:
                        continue
                    matched.append(record)
        return matched
