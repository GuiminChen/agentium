"""SQLite-backed chat session metadata (per-tenant lifecycle)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

import sqlite3


@dataclass(frozen=True)
class ChatSessionRecord:
    """Resolved chat session row for control-plane exposure."""

    tenant_id: str
    session_id: str
    title: Optional[str]
    skill: Optional[str]
    intro_text: Optional[str]
    metadata: Dict[str, Any]
    created_at: str
    updated_at: str
    deleted_at: Optional[str]


class SqliteChatSessionStore:
    """CRUD + soft delete for chat sessions (``session_id`` maps to ``run_id`` MVP)."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    tenant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    title TEXT,
                    skill TEXT,
                    intro_text TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT,
                    PRIMARY KEY (tenant_id, session_id)
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_sessions_tenant_updated "
                "ON chat_sessions(tenant_id, updated_at DESC)"
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def create_session(
        self,
        *,
        tenant_id: str,
        session_id: Optional[str],
        title: Optional[str],
        skill: Optional[str],
        intro_text: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> ChatSessionRecord:
        """Insert a new session row;raises ``sqlite3.IntegrityError`` when session exists."""

        sid = (session_id or "").strip() or str(uuid.uuid4())
        md = metadata if metadata is not None else {}
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO chat_sessions (
                    tenant_id, session_id, title, skill, intro_text,
                    metadata_json, created_at, updated_at, deleted_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    tenant_id,
                    sid,
                    title,
                    skill,
                    intro_text,
                    json.dumps(md, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            self._connection.commit()
        return self.get_session(tenant_id=tenant_id, session_id=sid)

    def get_session(self, *, tenant_id: str, session_id: str) -> ChatSessionRecord:
        """Fetch one non-deleted session."""

        with self._lock:
            row = self._connection.execute(
                """
                SELECT tenant_id, session_id, title, skill, intro_text,
                       metadata_json, created_at, updated_at, deleted_at
                FROM chat_sessions
                WHERE tenant_id = ? AND session_id = ? AND deleted_at IS NULL
                """,
                (tenant_id, session_id),
            ).fetchone()
        if row is None:
            raise KeyError("session_not_found")
        return self._row_to_record(row)

    def try_get_session(self, *, tenant_id: str, session_id: str) -> Optional[ChatSessionRecord]:
        """Return record or ``None`` if missing."""

        try:
            rec = self.get_session(tenant_id=tenant_id, session_id=session_id)
            return rec
        except KeyError:
            return None

    def update_session(
        self,
        *,
        tenant_id: str,
        session_id: str,
        title: Optional[str],
    ) -> ChatSessionRecord:
        """Update editable fields."""

        row = self.get_session(tenant_id=tenant_id, session_id=session_id)
        if row.deleted_at:
            raise KeyError("session_deleted")
        now = datetime.now(timezone.utc).isoformat()
        new_title = title if title is not None else row.title
        with self._lock:
            self._connection.execute(
                """
                UPDATE chat_sessions
                SET title = ?, updated_at = ?
                WHERE tenant_id = ? AND session_id = ? AND deleted_at IS NULL
                """,
                (new_title, now, tenant_id, session_id),
            )
            self._connection.commit()
        return self.get_session(tenant_id=tenant_id, session_id=session_id)

    def soft_delete(self, *, tenant_id: str, session_id: str) -> None:
        """Set ``deleted_at`` for the tenant's session."""

        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE chat_sessions
                SET deleted_at = ?, updated_at = ?
                WHERE tenant_id = ? AND session_id = ? AND deleted_at IS NULL
                """,
                (now, now, tenant_id, session_id),
            )
            self._connection.commit()
            if cursor.rowcount != 1:
                raise KeyError("session_not_found")

    def touch_updated_at(self, *, tenant_id: str, session_id: str) -> None:
        """Bump ``updated_at`` after a conversational turn."""

        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE chat_sessions
                SET updated_at = ?
                WHERE tenant_id = ? AND session_id = ? AND deleted_at IS NULL
                """,
                (now, tenant_id, session_id),
            )
            self._connection.commit()
            if cursor.rowcount != 1:
                raise KeyError("session_not_found")

    def list_sessions_page(
        self,
        *,
        tenant_id: str,
        page: int,
        page_size: int,
    ) -> tuple[List[ChatSessionRecord], Dict[str, Any]]:
        """Return paginated sessions (non-deleted) ordered by ``updated_at`` descending."""

        page = max(1, page)
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size
        with self._lock:
            count_row = self._connection.execute(
                """
                SELECT COUNT(*) AS c FROM chat_sessions
                WHERE tenant_id = ? AND deleted_at IS NULL
                """,
                (tenant_id,),
            ).fetchone()
            total = int(count_row["c"]) if count_row else 0
            rows = self._connection.execute(
                """
                SELECT tenant_id, session_id, title, skill, intro_text,
                       metadata_json, created_at, updated_at, deleted_at
                FROM chat_sessions
                WHERE tenant_id = ? AND deleted_at IS NULL
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (tenant_id, page_size, offset),
            ).fetchall()
        items = [self._row_to_record(r) for r in rows]
        total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
        pagination = {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        }
        return items, pagination

    def session_exists(self, *, tenant_id: str, session_id: str) -> bool:
        """Return whether a non-deleted session row exists."""

        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM chat_sessions
                WHERE tenant_id = ? AND session_id = ? AND deleted_at IS NULL
                """,
                (tenant_id, session_id),
            ).fetchone()
        return row is not None

    def _row_to_record(self, row: sqlite3.Row) -> ChatSessionRecord:
        raw_meta = row["metadata_json"] or "{}"
        try:
            meta = json.loads(raw_meta)
        except json.JSONDecodeError:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        return ChatSessionRecord(
            tenant_id=str(row["tenant_id"]),
            session_id=str(row["session_id"]),
            title=row["title"],
            skill=row["skill"],
            intro_text=row["intro_text"],
            metadata=meta,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            deleted_at=row["deleted_at"],
        )
