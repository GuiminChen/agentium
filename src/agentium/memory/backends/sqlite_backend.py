"""SQLite backend for the layered MemoryService."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import List

from agentium.memory.types import MemoryLayer, MemoryRecord


class SqliteMemoryBackend:
    """Persistent SQLite-backed memory with strict tenant isolation."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    layer TEXT NOT NULL,
                    key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_tenant_layer ON memory_records(tenant_id, layer)"
            )
            self._connection.commit()

    def append(self, record: MemoryRecord) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO memory_records (tenant_id, layer, key, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.tenant_id,
                    record.layer.value,
                    record.key,
                    json.dumps(record.payload, ensure_ascii=False),
                    record.created_at.isoformat(),
                ),
            )
            self._connection.commit()

    def query(
        self,
        tenant_id: str,
        layer: MemoryLayer,
        limit: int = 50,
    ) -> List[MemoryRecord]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT tenant_id, layer, key, payload_json, created_at
                FROM memory_records
                WHERE tenant_id = ? AND layer = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (tenant_id, layer.value, max(1, int(limit))),
            ).fetchall()
        records: List[MemoryRecord] = []
        for row in rows:
            records.append(
                MemoryRecord(
                    tenant_id=row["tenant_id"],
                    layer=MemoryLayer(row["layer"]),
                    key=row["key"],
                    payload=json.loads(row["payload_json"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        records.reverse()
        return records

    def purge_tenant(self, tenant_id: str) -> int:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM memory_records WHERE tenant_id = ?",
                (tenant_id,),
            )
            self._connection.commit()
            return cursor.rowcount or 0

    def close(self) -> None:
        with self._lock:
            try:
                self._connection.close()
            except Exception:
                pass
