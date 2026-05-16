"""Persistent wiki ingest job metadata (queued → succeeded/failed)."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "SqliteWikiIngestJobStore",
    "WikiIngestJobRecord",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class WikiIngestJobRecord:
    """One row in ``wiki_ingest_jobs``."""

    job_id: str
    tenant_id: str
    session_id: str
    blob_key: str
    status: str
    error: str
    created_at: str
    updated_at: str
    vault_key_hint: str


class SqliteWikiIngestJobStore:
    """CRUD in the main Agentium SQLite database."""

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path).expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._path))

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wiki_ingest_jobs (
                    job_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    blob_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    vault_key_hint TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def create_job(
        self,
        *,
        tenant_id: str,
        blob_key: str,
        session_id: str = "",
        vault_key_hint: str = "",
    ) -> str:
        job_id = str(uuid.uuid4())
        now = _now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO wiki_ingest_jobs
                (job_id, tenant_id, session_id, blob_key, status, error, created_at, updated_at, vault_key_hint)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    tenant_id,
                    session_id,
                    blob_key,
                    "queued",
                    "",
                    now,
                    now,
                    vault_key_hint,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return job_id

    def update_status(
        self,
        job_id: str,
        *,
        status: str,
        error: str = "",
    ) -> None:
        now = _now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE wiki_ingest_jobs
                SET status = ?, error = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status, error, now, job_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_job(self, job_id: str) -> Optional[WikiIngestJobRecord]:
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                SELECT job_id, tenant_id, session_id, blob_key, status, error,
                       created_at, updated_at, vault_key_hint
                FROM wiki_ingest_jobs WHERE job_id = ?
                """,
                (job_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return WikiIngestJobRecord(
            job_id=str(row[0]),
            tenant_id=str(row[1]),
            session_id=str(row[2]),
            blob_key=str(row[3]),
            status=str(row[4]),
            error=str(row[5]),
            created_at=str(row[6]),
            updated_at=str(row[7]),
            vault_key_hint=str(row[8]),
        )

    def list_recent_non_terminal_jobs_for_session(
        self,
        tenant_id: str,
        session_id: str,
        *,
        max_age_seconds: int,
        limit: int = 64,
    ) -> List[WikiIngestJobRecord]:
        """Jobs still queued/running for a chat session, newer than *max_age_seconds*."""

        sid = session_id.strip()
        if not sid:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        cutoff_iso = cutoff.replace(microsecond=0).isoformat()
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                SELECT job_id, tenant_id, session_id, blob_key, status, error,
                       created_at, updated_at, vault_key_hint
                FROM wiki_ingest_jobs
                WHERE tenant_id = ? AND session_id = ?
                  AND status IN ('queued', 'running')
                  AND created_at >= ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (tenant_id.strip(), sid, cutoff_iso, limit),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        out: List[WikiIngestJobRecord] = []
        for row in rows:
            out.append(
                WikiIngestJobRecord(
                    job_id=str(row[0]),
                    tenant_id=str(row[1]),
                    session_id=str(row[2]),
                    blob_key=str(row[3]),
                    status=str(row[4]),
                    error=str(row[5]),
                    created_at=str(row[6]),
                    updated_at=str(row[7]),
                    vault_key_hint=str(row[8]),
                )
            )
        return out

    def verify_jobs_succeeded_for_tenant(
        self,
        tenant_id: str,
        job_ids: Sequence[str],
    ) -> tuple[bool, List[Dict[str, Any]]]:
        """Return whether every id exists for *tenant_id* with ``status=succeeded``."""

        tid = tenant_id.strip()
        summaries: List[Dict[str, Any]] = []
        ok_all = True
        for jid_raw in job_ids:
            jid = str(jid_raw).strip()
            if not jid:
                ok_all = False
                summaries.append(
                    {"job_id": jid, "status": "invalid", "detail": "empty_job_id"}
                )
                continue
            rec = self.get_job(jid)
            if rec is None:
                ok_all = False
                summaries.append({"job_id": jid, "status": "missing"})
                continue
            if rec.tenant_id != tid:
                ok_all = False
                summaries.append(
                    {
                        "job_id": jid,
                        "status": "wrong_tenant",
                        "detail": rec.tenant_id,
                    }
                )
                continue
            if rec.status != "succeeded":
                ok_all = False
                summaries.append({"job_id": jid, "status": rec.status})
                continue
            summaries.append({"job_id": jid, "status": "succeeded"})
        return ok_all, summaries
