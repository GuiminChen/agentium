"""SQLite task lock backend (WAL, exclusive transactions)."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

import structlog

from agentium.coordination.task_lock.types import TaskLockLease

_LOGGER = structlog.get_logger(__name__)


class SqliteTaskLockBackend:
    """At most one active lease per ``(tenant_id, resource_key)``."""

    def __init__(self, *, path: Path) -> None:
        self._path = path
        self._guard = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=30.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_lock_lease (
                  tenant_id TEXT NOT NULL,
                  resource_key TEXT NOT NULL,
                  holder_run_id TEXT NOT NULL,
                  expires_at REAL NOT NULL,
                  issued_at REAL NOT NULL,
                  metadata_json TEXT,
                  PRIMARY KEY (tenant_id, resource_key)
                )
                """
            )

    def try_acquire(
        self,
        *,
        tenant_id: str,
        resource_key: str,
        holder_run_id: str,
        ttl_seconds: float,
        metadata_json: Optional[str] = None,
    ) -> Optional[TaskLockLease]:
        now = time.time()
        exp = now + max(0.001, float(ttl_seconds))
        tid = str(tenant_id).strip()
        rkey = str(resource_key).strip()
        hid = str(holder_run_id).strip()
        if not tid or not rkey or not hid:
            return None
        meta = metadata_json
        with self._guard, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "DELETE FROM task_lock_lease WHERE tenant_id = ? AND resource_key = ? AND expires_at <= ?",
                (tid, rkey, now),
            )
            row = c.execute(
                "SELECT holder_run_id, expires_at, issued_at+0 FROM task_lock_lease WHERE tenant_id = ? AND resource_key = ?",
                (tid, rkey),
            ).fetchone()
            if row is not None:
                old_holder = str(row[0])
                old_exp = float(row[1])
                if old_exp > now and old_holder != hid:
                    c.execute("ROLLBACK")
                    _LOGGER.info(
                        "task_lock_denied",
                        tenant_id=tid,
                        resource_key=rkey,
                        holder_run_id=hid,
                        reason="held_by_other",
                    )
                    return None
                if old_holder == hid:
                    c.execute(
                        """
                        UPDATE task_lock_lease
                        SET expires_at = ?, metadata_json = COALESCE(?, metadata_json)
                        WHERE tenant_id = ? AND resource_key = ?
                        """,
                        (exp, meta, tid, rkey),
                    )
                    c.execute("COMMIT")
                    lease = TaskLockLease(
                        tenant_id=tid,
                        resource_key=rkey,
                        holder_run_id=hid,
                        issued_at=float(row[2]),
                        expires_at=exp,
                    )
                    _LOGGER.info(
                        "task_lock_acquire",
                        tenant_id=tid,
                        resource_key=rkey,
                        holder_run_id=hid,
                        reissued="renew_same_holder",
                    )
                    return lease
            c.execute(
                """
                INSERT INTO task_lock_lease(
                  tenant_id, resource_key, holder_run_id, expires_at, issued_at, metadata_json
                ) VALUES (?,?,?,?,?,?)
                """,
                (tid, rkey, hid, exp, now, meta),
            )
            c.execute("COMMIT")
        lease = TaskLockLease(
            tenant_id=tid, resource_key=rkey, holder_run_id=hid, issued_at=now, expires_at=exp
        )
        _LOGGER.info("task_lock_acquire", tenant_id=tid, resource_key=rkey, holder_run_id=hid)
        return lease

    def renew(
        self,
        *,
        tenant_id: str,
        resource_key: str,
        holder_run_id: str,
        ttl_seconds: float,
    ) -> Optional[TaskLockLease]:
        now = time.time()
        exp = now + max(0.001, float(ttl_seconds))
        tid = str(tenant_id).strip()
        rkey = str(resource_key).strip()
        hid = str(holder_run_id).strip()
        with self._guard, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                """
                SELECT holder_run_id, expires_at, issued_at+0 FROM task_lock_lease
                WHERE tenant_id = ? AND resource_key = ?
                """,
                (tid, rkey),
            ).fetchone()
            if row is None or str(row[0]) != hid or float(row[1]) <= now:
                c.execute("ROLLBACK")
                return None
            c.execute(
                "UPDATE task_lock_lease SET expires_at = ? WHERE tenant_id = ? AND resource_key = ?",
                (exp, tid, rkey),
            )
            c.execute("COMMIT")
        _LOGGER.info("task_lock_acquire", tenant_id=tid, resource_key=rkey, holder_run_id=hid, phase="renew")
        return TaskLockLease(
            tenant_id=tid,
            resource_key=rkey,
            holder_run_id=hid,
            issued_at=float(row[2]),
            expires_at=exp,
        )

    def release(self, *, tenant_id: str, resource_key: str, holder_run_id: str) -> bool:
        tid = str(tenant_id).strip()
        rkey = str(resource_key).strip()
        hid = str(holder_run_id).strip()
        with self._guard, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT holder_run_id FROM task_lock_lease WHERE tenant_id = ? AND resource_key = ?",
                (tid, rkey),
            ).fetchone()
            if row is None or str(row[0]) != hid:
                c.execute("ROLLBACK")
                return False
            c.execute(
                "DELETE FROM task_lock_lease WHERE tenant_id = ? AND resource_key = ?",
                (tid, rkey),
            )
            c.execute("COMMIT")
        _LOGGER.info("task_lock_release", tenant_id=tid, resource_key=rkey, holder_run_id=hid)
        return True
