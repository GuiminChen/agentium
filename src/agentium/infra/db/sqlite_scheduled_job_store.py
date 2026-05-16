"""SQLite persistence for scheduled jobs and run ledger."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import sqlite3

from agentium.coordination.scheduled_job_schedule import initial_next_unix_ms, next_unix_ms_after
from agentium.models.scheduled_job import JobRunStatus

_ISO_NOW = lambda: datetime.now(timezone.utc).isoformat()


def compute_initial_next_run_at_unix_ms(trigger: Dict[str, Any], now_ms: int) -> Optional[int]:
    """Earliest fire time for a newly created job."""

    return initial_next_unix_ms(trigger, now_unix_ms=now_ms)


def _utc_ms_now() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


@dataclass(frozen=True)
class ScheduledJobRow:
    """Internal row mirror."""

    job_id: str
    tenant_id: str
    user_id: str
    name: str
    enabled: bool
    task_kind: str
    trigger_json: str
    session_binding: str
    pinned_session_id: Optional[str]
    payload_json: str
    policy_bundle_ref: Optional[str]
    budget_estimate_tokens: Optional[int]
    max_retries: int
    timeout_seconds: float
    next_run_at_unix_ms: Optional[int]
    last_run_at_unix_ms: Optional[int]
    created_at: str
    updated_at: str

    def as_public_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "name": self.name,
            "enabled": self.enabled,
            "task_kind": self.task_kind,
            "trigger": json.loads(self.trigger_json),
            "session_binding": self.session_binding,
            "pinned_session_id": self.pinned_session_id,
            "payload": json.loads(self.payload_json),
            "policy_bundle_ref": self.policy_bundle_ref,
            "budget_estimate_tokens": self.budget_estimate_tokens,
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
            "next_run_at_unix_ms": self.next_run_at_unix_ms,
            "last_run_at_unix_ms": self.last_run_at_unix_ms,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ScheduledJobRunRow:
    run_id: str
    job_id: str
    tenant_id: str
    status: str
    attempt_no: int
    trace_id: str
    session_id: Optional[str]
    error_detail: Optional[str]
    started_at: str
    finished_at: Optional[str]

    def as_public_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "job_id": self.job_id,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "attempt_no": self.attempt_no,
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "error_detail": self.error_detail,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class SqliteScheduledJobStore:
    """CRUD + atomic claim for due jobs."""

    def __init__(self, db_path: Any) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = Lock()
        self._ensure_schema()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    job_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    task_kind TEXT NOT NULL,
                    trigger_json TEXT NOT NULL,
                    session_binding TEXT NOT NULL,
                    pinned_session_id TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    policy_bundle_ref TEXT,
                    max_retries INTEGER NOT NULL DEFAULT 0,
                    timeout_seconds REAL NOT NULL DEFAULT 120,
                    next_run_at_unix_ms INTEGER,
                    last_run_at_unix_ms INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_job_runs (
                    run_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL DEFAULT 1,
                    trace_id TEXT NOT NULL,
                    session_id TEXT,
                    error_detail TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sched_jobs_tenant_next "
                "ON scheduled_jobs(tenant_id, next_run_at_unix_ms)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sched_runs_job_started "
                "ON scheduled_job_runs(job_id, started_at DESC)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sched_runs_tenant_status "
                "ON scheduled_job_runs(tenant_id, status)"
            )
            self._migrate_scheduled_jobs_columns_unlocked()
            self._migrate_webhook_idempotency_unlocked()
            self._connection.commit()

    def _migrate_scheduled_jobs_columns_unlocked(self) -> None:
        """Apply additive SQLite migrations for ``scheduled_jobs``."""

        cur = self._connection.execute("PRAGMA table_info(scheduled_jobs)")
        cols = {str(row[1]) for row in cur.fetchall()}
        if "budget_estimate_tokens" not in cols:
            self._connection.execute(
                "ALTER TABLE scheduled_jobs ADD COLUMN budget_estimate_tokens INTEGER"
            )

    def _migrate_webhook_idempotency_unlocked(self) -> None:
        """Dedupe table for ``POST /v1/jobs/webhook-trigger`` optional Idempotency-Key."""

        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_job_webhook_idempotency (
                tenant_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                received_unix_ms INTEGER NOT NULL,
                PRIMARY KEY (tenant_id, job_id, idempotency_key)
            )
            """
        )

    @staticmethod
    def _row_job(r: sqlite3.Row) -> ScheduledJobRow:
        return ScheduledJobRow(
            job_id=str(r["job_id"]),
            tenant_id=str(r["tenant_id"]),
            user_id=str(r["user_id"]),
            name=str(r["name"]),
            enabled=bool(r["enabled"]),
            task_kind=str(r["task_kind"]),
            trigger_json=str(r["trigger_json"]),
            session_binding=str(r["session_binding"]),
            pinned_session_id=(
                str(r["pinned_session_id"]) if r["pinned_session_id"] is not None else None
            ),
            payload_json=str(r["payload_json"]),
            policy_bundle_ref=(
                str(r["policy_bundle_ref"]) if r["policy_bundle_ref"] is not None else None
            ),
            budget_estimate_tokens=(
                int(r["budget_estimate_tokens"])
                if r["budget_estimate_tokens"] is not None
                else None
            ),
            max_retries=int(r["max_retries"]),
            timeout_seconds=float(r["timeout_seconds"]),
            next_run_at_unix_ms=(
                int(r["next_run_at_unix_ms"]) if r["next_run_at_unix_ms"] is not None else None
            ),
            last_run_at_unix_ms=(
                int(r["last_run_at_unix_ms"]) if r["last_run_at_unix_ms"] is not None else None
            ),
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
        )

    @staticmethod
    def _row_run(r: sqlite3.Row) -> ScheduledJobRunRow:
        return ScheduledJobRunRow(
            run_id=str(r["run_id"]),
            job_id=str(r["job_id"]),
            tenant_id=str(r["tenant_id"]),
            status=str(r["status"]),
            attempt_no=int(r["attempt_no"]),
            trace_id=str(r["trace_id"]),
            session_id=str(r["session_id"]) if r["session_id"] is not None else None,
            error_detail=str(r["error_detail"]) if r["error_detail"] is not None else None,
            started_at=str(r["started_at"]),
            finished_at=str(r["finished_at"]) if r["finished_at"] is not None else None,
        )

    def insert_job(
        self,
        *,
        tenant_id: str,
        user_id: str,
        name: str,
        enabled: bool,
        task_kind: str,
        trigger: Dict[str, Any],
        session_binding: str,
        pinned_session_id: Optional[str],
        payload: Dict[str, Any],
        policy_bundle_ref: Optional[str],
        budget_estimate_tokens: Optional[int],
        max_retries: int,
        timeout_seconds: float,
        next_run_at_unix_ms: Optional[int],
    ) -> ScheduledJobRow:
        job_id = str(uuid.uuid4())
        now = _ISO_NOW()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO scheduled_jobs (
                    job_id, tenant_id, user_id, name, enabled, task_kind,
                    trigger_json, session_binding, pinned_session_id,
                    payload_json, policy_bundle_ref, budget_estimate_tokens,
                    max_retries, timeout_seconds,
                    next_run_at_unix_ms, last_run_at_unix_ms, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job_id,
                    tenant_id,
                    user_id,
                    name,
                    1 if enabled else 0,
                    task_kind,
                    json.dumps(trigger, ensure_ascii=False),
                    session_binding,
                    pinned_session_id,
                    json.dumps(payload, ensure_ascii=False),
                    policy_bundle_ref,
                    budget_estimate_tokens,
                    max_retries,
                    timeout_seconds,
                    next_run_at_unix_ms,
                    None,
                    now,
                    now,
                ),
            )
            self._connection.commit()
        got = self.get_job(job_id=job_id, tenant_id=tenant_id)
        assert got is not None
        return got

    def get_job(self, *, job_id: str, tenant_id: str) -> Optional[ScheduledJobRow]:
        with self._lock:
            cur = self._connection.execute(
                "SELECT * FROM scheduled_jobs WHERE job_id = ? AND tenant_id = ?",
                (job_id, tenant_id),
            )
            r = cur.fetchone()
        return self._row_job(r) if r else None

    def delete_job(self, *, job_id: str, tenant_id: str) -> bool:
        with self._lock:
            cur = self._connection.execute(
                "DELETE FROM scheduled_jobs WHERE job_id = ? AND tenant_id = ?",
                (job_id, tenant_id),
            )
            self._connection.commit()
            return cur.rowcount > 0

    def list_jobs(
        self,
        *,
        tenant_id: str,
        page: int,
        page_size: int,
    ) -> Tuple[List[ScheduledJobRow], int]:
        if page < 1:
            page = 1
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size
        with self._lock:
            cur = self._connection.execute(
                "SELECT COUNT(*) FROM scheduled_jobs WHERE tenant_id = ?",
                (tenant_id,),
            )
            total = int(cur.fetchone()[0])
            cur = self._connection.execute(
                """
                SELECT * FROM scheduled_jobs
                WHERE tenant_id = ?
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (tenant_id, page_size, offset),
            )
            rows = [self._row_job(r) for r in cur.fetchall()]
        return rows, total

    def patch_job(
        self,
        *,
        job_id: str,
        tenant_id: str,
        updates: Dict[str, Any],
    ) -> Optional[ScheduledJobRow]:
        """Apply partial updates; caller validates contents."""

        fields = []
        values: List[Any] = []
        if "name" in updates and updates["name"] is not None:
            fields.append("name = ?")
            values.append(updates["name"])
        if "enabled" in updates and updates["enabled"] is not None:
            fields.append("enabled = ?")
            values.append(1 if updates["enabled"] else 0)
        if "trigger" in updates and updates["trigger"] is not None:
            fields.append("trigger_json = ?")
            values.append(json.dumps(updates["trigger"], ensure_ascii=False))
        if "session_binding" in updates and updates["session_binding"] is not None:
            fields.append("session_binding = ?")
            values.append(updates["session_binding"])
        if "pinned_session_id" in updates:
            fields.append("pinned_session_id = ?")
            values.append(updates["pinned_session_id"])
        if "payload" in updates and updates["payload"] is not None:
            fields.append("payload_json = ?")
            values.append(json.dumps(updates["payload"], ensure_ascii=False))
        if "policy_bundle_ref" in updates:
            fields.append("policy_bundle_ref = ?")
            values.append(updates["policy_bundle_ref"])
        if "budget_estimate_tokens" in updates:
            fields.append("budget_estimate_tokens = ?")
            values.append(updates["budget_estimate_tokens"])
        if "max_retries" in updates and updates["max_retries"] is not None:
            fields.append("max_retries = ?")
            values.append(int(updates["max_retries"]))
        if "timeout_seconds" in updates and updates["timeout_seconds"] is not None:
            fields.append("timeout_seconds = ?")
            values.append(float(updates["timeout_seconds"]))
        if "next_run_at_unix_ms" in updates:
            fields.append("next_run_at_unix_ms = ?")
            values.append(updates["next_run_at_unix_ms"])
        if not fields:
            return self.get_job(job_id=job_id, tenant_id=tenant_id)
        now = _ISO_NOW()
        fields.append("updated_at = ?")
        values.append(now)
        values.extend([job_id, tenant_id])
        with self._lock:
            cur = self._connection.execute(
                f"UPDATE scheduled_jobs SET {', '.join(fields)} WHERE job_id = ? AND tenant_id = ?",
                tuple(values),
            )
            self._connection.commit()
            if cur.rowcount == 0:
                return None
        return self.get_job(job_id=job_id, tenant_id=tenant_id)

    def claim_webhook_idempotency_key(
        self,
        *,
        tenant_id: str,
        job_id: str,
        idempotency_key: str,
        received_unix_ms: int,
    ) -> bool:
        """Record webhook ``Idempotency-Key`` for deduplication.

        Args:
            tenant_id: Tenant scope (from JSON body).
            job_id: Job id (from JSON body).
            idempotency_key: Caller-supplied key (trimmed); empty keys are ignored (always True).
            received_unix_ms: Wall clock for auditing.

        Returns:
            True when this is the first delivery for the triple; False when duplicate.
        """

        key = idempotency_key.strip()
        if not key:
            return True
        with self._lock:
            cur = self._connection.execute(
                """
                INSERT OR IGNORE INTO scheduled_job_webhook_idempotency (
                    tenant_id, job_id, idempotency_key, received_unix_ms
                ) VALUES (?,?,?,?)
                """,
                (tenant_id, job_id, key, received_unix_ms),
            )
            self._connection.commit()
            return bool(cur.rowcount == 1)

    def insert_run(
        self,
        *,
        run_id: str,
        job_id: str,
        tenant_id: str,
        status: JobRunStatus,
        attempt_no: int,
        trace_id: str,
        session_id: Optional[str],
    ) -> None:
        now = _ISO_NOW()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO scheduled_job_runs (
                    run_id, job_id, tenant_id, status, attempt_no,
                    trace_id, session_id, error_detail, started_at, finished_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    job_id,
                    tenant_id,
                    status,
                    attempt_no,
                    trace_id,
                    session_id,
                    None,
                    now,
                    None,
                ),
            )
            self._connection.commit()

    def finish_run(
        self,
        *,
        run_id: str,
        status: JobRunStatus,
        error_detail: Optional[str],
    ) -> None:
        now = _ISO_NOW()
        with self._lock:
            self._connection.execute(
                """
                UPDATE scheduled_job_runs
                SET status = ?, error_detail = ?, finished_at = ?
                WHERE run_id = ?
                """,
                (status, error_detail, now, run_id),
            )
            self._connection.commit()

    def update_run_session(self, *, run_id: str, session_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE scheduled_job_runs SET session_id = ? WHERE run_id = ?",
                (session_id, run_id),
            )
            self._connection.commit()

    def list_runs(
        self,
        *,
        tenant_id: str,
        job_id: str,
        page: int,
        page_size: int,
        status_filter: Optional[str] = None,
        started_after: Optional[str] = None,
        started_before: Optional[str] = None,
    ) -> Tuple[List[ScheduledJobRunRow], int]:
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size
        where = "tenant_id = ? AND job_id = ?"
        args: List[Any] = [tenant_id, job_id]
        if status_filter:
            where += " AND status = ?"
            args.append(status_filter)
        if started_after:
            where += " AND started_at >= ?"
            args.append(started_after)
        if started_before:
            where += " AND started_at <= ?"
            args.append(started_before)
        with self._lock:
            cur = self._connection.execute(
                f"SELECT COUNT(*) FROM scheduled_job_runs WHERE {where}",
                tuple(args),
            )
            total = int(cur.fetchone()[0])
            cur = self._connection.execute(
                f"""
                SELECT * FROM scheduled_job_runs
                WHERE {where}
                ORDER BY started_at DESC
                LIMIT ? OFFSET ?
                """,
                tuple(args + [page_size, offset]),
            )
            rows = [self._row_run(r) for r in cur.fetchall()]
        return rows, total

    def try_claim_due_job(self, *, now_ms: Optional[int] = None) -> Optional[ScheduledJobRow]:
        """Pick one due enabled job, atomically advance schedule or disable one-shot.

        Returns the job snapshot **before** schedule mutation (for executor trigger payload),
        together with the scheduled next value applied — caller uses returned row's trigger
        to compute session and payload.

        Implementation: retry loop selecting candidates ordered by next_run_at.
        """

        ts = now_ms if now_ms is not None else _utc_ms_now()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                cur = self._connection.execute(
                    """
                    SELECT * FROM scheduled_jobs
                    WHERE enabled = 1 AND next_run_at_unix_ms IS NOT NULL
                      AND next_run_at_unix_ms <= ?
                    ORDER BY next_run_at_unix_ms ASC
                    LIMIT 1
                    """,
                    (ts,),
                )
                r = cur.fetchone()
                if r is None:
                    self._connection.commit()
                    return None
                row = self._row_job(r)
                trigger = json.loads(row.trigger_json)
                kind = trigger.get("kind")
                prev_next = row.next_run_at_unix_ms
                try:
                    new_next = next_unix_ms_after(trigger, after_unix_ms=ts)
                except ValueError:
                    self._connection.rollback()
                    return None
                if kind == "one_shot":
                    next_enabled = 0
                else:
                    next_enabled = 1 if row.enabled else 0
                cur2 = self._connection.execute(
                    """
                    UPDATE scheduled_jobs
                    SET next_run_at_unix_ms = ?,
                        enabled = ?,
                        last_run_at_unix_ms = ?,
                        updated_at = ?
                    WHERE job_id = ? AND tenant_id = ?
                      AND next_run_at_unix_ms IS NOT NULL
                      AND next_run_at_unix_ms = ?
                    """,
                    (
                        new_next,
                        next_enabled,
                        ts,
                        _ISO_NOW(),
                        row.job_id,
                        row.tenant_id,
                        prev_next,
                    ),
                )
                if cur2.rowcount != 1:
                    self._connection.rollback()
                    return None
                self._connection.commit()
                return row
            except Exception:
                self._connection.rollback()
                raise


__all__ = [
    "ScheduledJobRow",
    "ScheduledJobRunRow",
    "SqliteScheduledJobStore",
    "compute_initial_next_run_at_unix_ms",
]
