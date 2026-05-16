"""SQLite persistence adapters for governance state."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional
from uuid import uuid4

from agentium.coordination.budget_ledger import BudgetUsage, TenantBudget
from agentium.governance.approval_gate import ApprovalRequest, ApprovalStatus
from agentium.models.context import AuditRecord, RequestContext
from agentium.shared.chat_timeline import CHAT_KIND_ASSISTANT, CHAT_KIND_USER


def _parse_iso_or_none(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class SqliteAuditSink:
    """Append-only audit sink backed by SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock:
            cursor = self._connection.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    policy_version TEXT,
                    payload_json TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_run_id ON audit_events(run_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_tenant_id ON audit_events(tenant_id)"
            )
            self._connection.commit()

    def append(self, record: AuditRecord) -> None:
        """Append one audit record to SQLite."""

        with self._lock:
            self._connection.execute(
                """
                INSERT INTO audit_events (
                    event_type, timestamp, tenant_id, run_id, policy_version, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.event_type,
                    record.timestamp.isoformat(),
                    record.tenant_id,
                    record.run_id,
                    record.policy_version,
                    json.dumps(record.payload, ensure_ascii=False),
                ),
            )
            self._connection.commit()

    def query(
        self, run_id: Optional[str] = None, tenant_id: Optional[str] = None
    ) -> List[AuditRecord]:
        """Query audit records by optional filters."""

        clauses = []
        params: List[str] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        where_clause = ""
        if clauses:
            where_clause = "WHERE " + " AND ".join(clauses)
        sql = (
            "SELECT event_type, timestamp, tenant_id, run_id, policy_version, payload_json "
            "FROM audit_events "
            + where_clause
            + " ORDER BY id ASC"
        )
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        records: List[AuditRecord] = []
        for row in rows:
            records.append(
                AuditRecord(
                    event_type=row["event_type"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    tenant_id=row["tenant_id"],
                    run_id=row["run_id"],
                    policy_version=row["policy_version"],
                    payload=json.loads(row["payload_json"]),
                )
            )
        return records

    def aggregate_recent_runs_for_tenant(
        self, tenant_id: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Return latest event per run for one tenant (production SQLite path)."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT e.run_id, e.event_type, e.timestamp
                FROM audit_events e
                INNER JOIN (
                    SELECT run_id, MAX(id) AS max_id
                    FROM audit_events
                    WHERE tenant_id = ?
                    GROUP BY run_id
                ) t ON e.run_id = t.run_id AND e.id = t.max_id
                ORDER BY e.timestamp DESC
                LIMIT ?
                """,
                (tenant_id, limit),
            ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "last_event_type": row["event_type"],
                "last_ts": row["timestamp"],
            }
            for row in rows
        ]

    def fetch_recent_channel_events(
        self, tenant_id: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Recent outbound channel audit rows for connectors inbox (PII-safe fields only)."""

        limit = max(1, min(200, limit))
        types = ("channel_delivered", "channel_failed", "channel_skipped")
        placeholders = ",".join("?" * len(types))
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT id, event_type, timestamp, run_id, tenant_id, payload_json
                FROM audit_events
                WHERE tenant_id = ? AND event_type IN ({placeholders})
                ORDER BY id DESC
                LIMIT ?
                """,
                (tenant_id, *types, limit),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            channel = ""
            if isinstance(payload.get("channel"), str):
                channel = payload["channel"]
            reason = ""
            if isinstance(payload.get("reason"), str):
                reason = payload["reason"][:256]
            out.append(
                {
                    "audit_id": int(row["id"]),
                    "event_type": row["event_type"],
                    "timestamp": row["timestamp"],
                    "run_id": row["run_id"],
                    "tenant_id": row["tenant_id"],
                    "channel": channel,
                    "reason": reason,
                }
            )
        return out

    def close(self) -> None:
        """Close SQLite connection."""

        with self._lock:
            self._connection.close()


class SqliteApprovalGate:
    """SQLite-backed approval gate for durable HITL workflow."""

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
                CREATE TABLE IF NOT EXISTS approval_requests (
                    approval_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    args_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    approver_id TEXT,
                    comment TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_approval_run_id ON approval_requests(run_id)"
            )
            existing_columns = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(approval_requests)"
                ).fetchall()
            }
            if "expires_at" not in existing_columns:
                self._connection.execute(
                    "ALTER TABLE approval_requests ADD COLUMN expires_at TEXT"
                )
            if "resume_state_json" not in existing_columns:
                self._connection.execute(
                    "ALTER TABLE approval_requests ADD COLUMN resume_state_json TEXT"
                )
            self._connection.commit()

    def request_approval(
        self,
        context: RequestContext,
        tool_name: str,
        reason: str,
        args_hash: str,
        ttl_seconds: Optional[int] = None,
        resume_state_json: Optional[str] = None,
    ) -> ApprovalRequest:
        """Create durable approval request."""

        from datetime import timedelta

        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires_at_dt: Optional[datetime] = None
        expires_at_str: Optional[str] = None
        if ttl_seconds and ttl_seconds > 0:
            expires_at_dt = now_dt + timedelta(seconds=ttl_seconds)
            expires_at_str = expires_at_dt.isoformat()
        approval_id = str(uuid4())
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO approval_requests (
                    approval_id, run_id, tenant_id, tool_name, reason, args_hash, status,
                    requested_by, approver_id, comment, created_at, updated_at,
                    expires_at, resume_state_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    context.run_id,
                    context.tenant_id,
                    tool_name,
                    reason,
                    args_hash,
                    ApprovalStatus.PENDING.value,
                    context.user_id,
                    None,
                    None,
                    now,
                    now,
                    expires_at_str,
                    resume_state_json,
                ),
            )
            self._connection.commit()
        return ApprovalRequest(
            approval_id=approval_id,
            run_id=context.run_id,
            tenant_id=context.tenant_id,
            tool_name=tool_name,
            reason=reason,
            args_hash=args_hash,
            status=ApprovalStatus.PENDING,
            requested_by=context.user_id,
            approver_id=None,
            comment=None,
            expires_at=expires_at_dt,
            resume_state_json=resume_state_json,
        )

    def expire_pending(self, now: Optional[datetime] = None) -> List[ApprovalRequest]:
        """Move pending approvals past their expires_at to EXPIRED."""

        cutoff = (now or datetime.now(timezone.utc)).isoformat()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT approval_id, run_id, tenant_id, tool_name, reason, args_hash, status,
                       requested_by, approver_id, comment, expires_at, resume_state_json
                FROM approval_requests
                WHERE status = ?
                  AND expires_at IS NOT NULL
                  AND expires_at <= ?
                """,
                (ApprovalStatus.PENDING.value, cutoff),
            ).fetchall()
            expired: List[ApprovalRequest] = []
            for row in rows:
                self._connection.execute(
                    "UPDATE approval_requests SET status = ?, updated_at = ? WHERE approval_id = ?",
                    (ApprovalStatus.EXPIRED.value, cutoff, row["approval_id"]),
                )
                expired.append(
                    ApprovalRequest(
                        approval_id=row["approval_id"],
                        run_id=row["run_id"],
                        tenant_id=row["tenant_id"],
                        tool_name=row["tool_name"],
                        reason=row["reason"],
                        args_hash=row["args_hash"],
                        status=ApprovalStatus.EXPIRED,
                        requested_by=row["requested_by"],
                        approver_id=row["approver_id"],
                        comment=row["comment"],
                        expires_at=_parse_iso_or_none(row["expires_at"]),
                        resume_state_json=row["resume_state_json"],
                    )
                )
            self._connection.commit()
        return expired

    def list_pending_for_run(self, run_id: str) -> List[ApprovalRequest]:
        """List all pending approvals for one run id."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT approval_id, run_id, tenant_id, tool_name, reason, args_hash, status,
                       requested_by, approver_id, comment, expires_at, resume_state_json
                FROM approval_requests
                WHERE run_id = ? AND status = ?
                """,
                (run_id, ApprovalStatus.PENDING.value),
            ).fetchall()
        results: List[ApprovalRequest] = []
        for row in rows:
            results.append(
                ApprovalRequest(
                    approval_id=row["approval_id"],
                    run_id=row["run_id"],
                    tenant_id=row["tenant_id"],
                    tool_name=row["tool_name"],
                    reason=row["reason"],
                    args_hash=row["args_hash"],
                    status=ApprovalStatus(row["status"]),
                    requested_by=row["requested_by"],
                    approver_id=row["approver_id"],
                    comment=row["comment"],
                    expires_at=_parse_iso_or_none(row["expires_at"]),
                    resume_state_json=row["resume_state_json"],
                )
            )
        return results

    def approve(self, approval_id: str, approver_id: str, comment: str = "") -> bool:
        """Approve a pending request."""

        return self._update_status(
            approval_id=approval_id,
            approver_id=approver_id,
            comment=comment,
            next_status=ApprovalStatus.APPROVED,
        )

    def reject(self, approval_id: str, approver_id: str, comment: str = "") -> bool:
        """Reject a pending request."""

        return self._update_status(
            approval_id=approval_id,
            approver_id=approver_id,
            comment=comment,
            next_status=ApprovalStatus.REJECTED,
        )

    def _update_status(
        self,
        approval_id: str,
        approver_id: str,
        comment: str,
        next_status: ApprovalStatus,
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            row = self._connection.execute(
                "SELECT status FROM approval_requests WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if row is None:
                return False
            if row["status"] != ApprovalStatus.PENDING.value:
                return False
            self._connection.execute(
                """
                UPDATE approval_requests
                SET status = ?, approver_id = ?, comment = ?, updated_at = ?
                WHERE approval_id = ?
                """,
                (
                    next_status.value,
                    approver_id,
                    comment or None,
                    now,
                    approval_id,
                ),
            )
            self._connection.commit()
            return True

    def get_request(self, approval_id: str) -> Optional[ApprovalRequest]:
        """Load one request by id."""

        with self._lock:
            row = self._connection.execute(
                """
                SELECT approval_id, run_id, tenant_id, tool_name, reason, args_hash, status,
                       requested_by, approver_id, comment, expires_at, resume_state_json
                FROM approval_requests
                WHERE approval_id = ?
                """,
                (approval_id,),
            ).fetchone()
        if row is None:
            return None
        return ApprovalRequest(
            approval_id=row["approval_id"],
            run_id=row["run_id"],
            tenant_id=row["tenant_id"],
            tool_name=row["tool_name"],
            reason=row["reason"],
            args_hash=row["args_hash"],
            status=ApprovalStatus(row["status"]),
            requested_by=row["requested_by"],
            approver_id=row["approver_id"],
            comment=row["comment"],
            expires_at=_parse_iso_or_none(row["expires_at"]),
            resume_state_json=row["resume_state_json"],
        )

    def list_approvals(
        self,
        *,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[ApprovalRequest]:
        """List durable approval rows with optional filters."""

        clauses: List[str] = []
        params: List[Any] = []
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT approval_id, run_id, tenant_id, tool_name, reason, args_hash, status, "
            "requested_by, approver_id, comment, expires_at, resume_state_json "
            "FROM approval_requests "
            + where
            + " ORDER BY created_at ASC"
        )
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        results: List[ApprovalRequest] = []
        for row in rows:
            results.append(
                ApprovalRequest(
                    approval_id=row["approval_id"],
                    run_id=row["run_id"],
                    tenant_id=row["tenant_id"],
                    tool_name=row["tool_name"],
                    reason=row["reason"],
                    args_hash=row["args_hash"],
                    status=ApprovalStatus(row["status"]),
                    requested_by=row["requested_by"],
                    approver_id=row["approver_id"],
                    comment=row["comment"],
                    expires_at=_parse_iso_or_none(row["expires_at"]),
                    resume_state_json=row["resume_state_json"],
                )
            )
        if limit > 0 and len(results) > limit:
            results = results[-limit:]
        return results

    def close(self) -> None:
        """Close SQLite connection."""

        with self._lock:
            self._connection.close()


class SqliteBudgetLedger:
    """SQLite-backed budget ledger with reservation durability."""

    def __init__(self, db_path: Path, tenant_budgets: Dict[str, TenantBudget]) -> None:
        self._db_path = db_path
        self._tenant_budgets = tenant_budgets
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS budget_usage (
                    tenant_id TEXT PRIMARY KEY,
                    tokens_used INTEGER NOT NULL,
                    cost_used REAL NOT NULL,
                    inflight_calls INTEGER NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS budget_reservations (
                    run_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    estimated_tokens INTEGER NOT NULL,
                    estimated_cost REAL NOT NULL
                )
                """
            )
            self._connection.commit()

    def reserve(
        self, context: RequestContext, estimated_tokens: int, estimated_cost: float
    ) -> bool:
        """Reserve tenant budget for one operation."""

        if estimated_tokens < 0 or estimated_cost < 0:
            return False
        with self._lock:
            budget = self._tenant_budgets.get(context.tenant_id)
            if budget is None:
                return False
            if self._has_reservation(context.run_id):
                return False
            usage = self._load_usage(context.tenant_id)
            if usage.inflight_calls >= budget.max_concurrency:
                return False
            projected_tokens = usage.tokens_used + estimated_tokens
            projected_cost = usage.cost_used + estimated_cost
            if projected_tokens > budget.token_limit or projected_cost > budget.cost_limit:
                return False
            self._save_usage(
                context.tenant_id,
                BudgetUsage(
                    tokens_used=projected_tokens,
                    cost_used=projected_cost,
                    inflight_calls=usage.inflight_calls + 1,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO budget_reservations (
                    run_id, tenant_id, estimated_tokens, estimated_cost
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    context.run_id,
                    context.tenant_id,
                    estimated_tokens,
                    estimated_cost,
                ),
            )
            self._connection.commit()
            return True

    def commit(
        self, context: RequestContext, actual_tokens: int, actual_cost: float
    ) -> None:
        """Commit reservation and finalize actual usage."""

        with self._lock:
            reservation = self._load_reservation(context.run_id)
            if reservation is None:
                return
            usage = self._load_usage(reservation["tenant_id"])
            usage.tokens_used += max(0, actual_tokens - reservation["estimated_tokens"])
            usage.cost_used += max(0.0, actual_cost - reservation["estimated_cost"])
            usage.inflight_calls = max(0, usage.inflight_calls - 1)
            self._save_usage(reservation["tenant_id"], usage)
            self._connection.execute(
                "DELETE FROM budget_reservations WHERE run_id = ?",
                (context.run_id,),
            )
            self._connection.commit()

    def release(self, context: RequestContext) -> None:
        """Release reservation without usage commit."""

        with self._lock:
            reservation = self._load_reservation(context.run_id)
            if reservation is None:
                return
            usage = self._load_usage(reservation["tenant_id"])
            usage.tokens_used = max(0, usage.tokens_used - reservation["estimated_tokens"])
            usage.cost_used = max(0.0, usage.cost_used - reservation["estimated_cost"])
            usage.inflight_calls = max(0, usage.inflight_calls - 1)
            self._save_usage(reservation["tenant_id"], usage)
            self._connection.execute(
                "DELETE FROM budget_reservations WHERE run_id = ?",
                (context.run_id,),
            )
            self._connection.commit()

    def usage_for_tenant(self, tenant_id: str) -> Optional[BudgetUsage]:
        """Return usage snapshot for one tenant."""

        with self._lock:
            row = self._connection.execute(
                """
                SELECT tokens_used, cost_used, inflight_calls
                FROM budget_usage
                WHERE tenant_id = ?
                """,
                (tenant_id,),
            ).fetchone()
        if row is None:
            return None
        return BudgetUsage(
            tokens_used=int(row["tokens_used"]),
            cost_used=float(row["cost_used"]),
            inflight_calls=int(row["inflight_calls"]),
        )

    def close(self) -> None:
        """Close SQLite connection."""

        with self._lock:
            self._connection.close()

    def _has_reservation(self, run_id: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM budget_reservations WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return row is not None

    def _load_usage(self, tenant_id: str) -> BudgetUsage:
        row = self._connection.execute(
            """
            SELECT tokens_used, cost_used, inflight_calls
            FROM budget_usage
            WHERE tenant_id = ?
            """,
            (tenant_id,),
        ).fetchone()
        if row is None:
            return BudgetUsage(tokens_used=0, cost_used=0.0, inflight_calls=0)
        return BudgetUsage(
            tokens_used=int(row["tokens_used"]),
            cost_used=float(row["cost_used"]),
            inflight_calls=int(row["inflight_calls"]),
        )

    def _save_usage(self, tenant_id: str, usage: BudgetUsage) -> None:
        self._connection.execute(
            """
            INSERT INTO budget_usage (tenant_id, tokens_used, cost_used, inflight_calls)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(tenant_id) DO UPDATE SET
                tokens_used = excluded.tokens_used,
                cost_used = excluded.cost_used,
                inflight_calls = excluded.inflight_calls
            """,
            (tenant_id, usage.tokens_used, usage.cost_used, usage.inflight_calls),
        )

    def _load_reservation(self, run_id: str) -> Optional[sqlite3.Row]:
        return self._connection.execute(
            """
            SELECT tenant_id, estimated_tokens, estimated_cost
            FROM budget_reservations
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()


class SqliteRunMessageStore:
    """Per-run chat timeline rows (session_id == run_id for MVP)."""

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
                CREATE TABLE IF NOT EXISTS run_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    tool_name TEXT,
                    status TEXT,
                    request_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, seq)
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_run_messages_run_seq ON run_messages(run_id, seq)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_run_messages_tenant ON run_messages(tenant_id)"
            )
            self._connection.commit()

    def append(
        self,
        *,
        run_id: str,
        tenant_id: str,
        role: str,
        kind: str,
        body: Dict[str, Any],
        tool_name: Optional[str] = None,
        status: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append one message; seq is monotonic per run_id."""

        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(seq), 0) AS m FROM run_messages WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            seq = int(row["m"]) + 1
            self._connection.execute(
                """
                INSERT INTO run_messages (
                    run_id, tenant_id, seq, role, kind, body_json,
                    tool_name, status, request_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    tenant_id,
                    seq,
                    role,
                    kind,
                    json.dumps(body, ensure_ascii=False),
                    tool_name,
                    status,
                    request_id,
                    created_at,
                ),
            )
            self._connection.commit()
        return {
            "seq": seq,
            "run_id": run_id,
            "tenant_id": tenant_id,
            "role": role,
            "kind": kind,
            "body": body,
            "tool_name": tool_name,
            "status": status,
            "request_id": request_id,
            "created_at": created_at,
        }

    def list_page(
        self,
        *,
        run_id: str,
        tenant_id: str,
        after_seq: int = 0,
        limit: int = 50,
    ) -> tuple[List[Dict[str, Any]], Optional[int]]:
        """Return messages with seq > after_seq, ordered by seq ASC."""

        limit = max(1, min(200, limit))
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT seq, role, kind, body_json, tool_name, status, request_id, created_at
                FROM run_messages
                WHERE run_id = ? AND tenant_id = ? AND seq > ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (run_id, tenant_id, after_seq, limit + 1),
            ).fetchall()
        items: List[Dict[str, Any]] = []
        for row in rows[:limit]:
            items.append(
                {
                    "seq": int(row["seq"]),
                    "role": row["role"],
                    "kind": row["kind"],
                    "body": json.loads(row["body_json"]),
                    "tool_name": row["tool_name"],
                    "status": row["status"],
                    "request_id": row["request_id"],
                    "created_at": row["created_at"],
                }
            )
        next_cursor: Optional[int] = None
        if len(rows) > limit:
            next_cursor = items[-1]["seq"] if items else None
        elif items:
            next_cursor = None
        return items, next_cursor

    def count_chat_assistant_rows(self, *, run_id: str, tenant_id: str) -> int:
        """Count completed chat turns (assistant rows) for pagination."""

        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) AS c FROM run_messages
                WHERE run_id = ? AND tenant_id = ? AND kind = ?
                """,
                (run_id, tenant_id, CHAT_KIND_ASSISTANT),
            ).fetchone()
        return int(row["c"]) if row else 0

    def list_chat_messages_page(
        self,
        *,
        run_id: str,
        tenant_id: str,
        page: int,
        page_size: int,
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """TradeAgent-style message list: one item per user/assistant pair ordered by turn (seq ASC).

        Pagination counts **assistant rows** (finished turns). Incomplete trailing user rows are omitted.
        """

        page = max(1, page)
        page_size = max(1, min(100, page_size))
        total = self.count_chat_assistant_rows(run_id=run_id, tenant_id=tenant_id)
        total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
        offset = (page - 1) * page_size
        pagination = {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        }
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT seq, body_json, status, request_id, created_at
                FROM run_messages
                WHERE run_id = ? AND tenant_id = ? AND kind = ?
                ORDER BY seq ASC
                LIMIT ? OFFSET ?
                """,
                (run_id, tenant_id, CHAT_KIND_ASSISTANT, page_size, offset),
            ).fetchall()

        items: List[Dict[str, Any]] = []
        for row in rows:
            body = json.loads(row["body_json"])
            pair_id = body.get("message_pair_id")
            if not isinstance(pair_id, str) or not pair_id:
                continue
            user_body: Dict[str, Any] = {}
            with self._lock:
                urow = self._connection.execute(
                    """
                    SELECT body_json FROM run_messages
                    WHERE run_id = ? AND tenant_id = ? AND kind = ?
                      AND json_extract(body_json, '$.message_pair_id') = ?
                    ORDER BY seq ASC
                    LIMIT 1
                    """,
                    (run_id, tenant_id, CHAT_KIND_USER, pair_id),
                ).fetchone()
            if urow is not None:
                try:
                    user_body = json.loads(urow["body_json"])
                except (TypeError, json.JSONDecodeError):
                    user_body = {}
            query_txt = user_body.get("content")
            if query_txt is None:
                query_txt = ""
            message_id = body.get("message_id") or pair_id
            items.append(
                {
                    "message_id": message_id,
                    "query": query_txt,
                    "answer": body.get("answer") or "",
                    "status": body.get("status") or row["status"] or "finished",
                    "content_blocks": body.get("content_blocks") or [],
                }
            )
        return items, pagination

    def list_recent_chat_rows(self, *, run_id: str, tenant_id: str, limit_rows: int) -> List[Dict[str, Any]]:
        """Return up to ``limit_rows`` chat timeline rows in chronological order (mixed kinds)."""

        cap = max(1, min(200, limit_rows))
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT seq, role, kind, body_json, tool_name, status, request_id, created_at
                FROM run_messages
                WHERE run_id = ? AND tenant_id = ? AND kind IN (?, ?)
                ORDER BY seq DESC
                LIMIT ?
                """,
                (run_id, tenant_id, CHAT_KIND_USER, CHAT_KIND_ASSISTANT, cap),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in reversed(rows):
            out.append(
                {
                    "seq": int(row["seq"]),
                    "role": row["role"],
                    "kind": row["kind"],
                    "body": json.loads(row["body_json"]),
                    "tool_name": row["tool_name"],
                    "status": row["status"],
                    "request_id": row["request_id"],
                    "created_at": row["created_at"],
                }
            )
        return out

    def delete_latest_chat_assistant_for_message_id(
        self,
        *,
        run_id: str,
        tenant_id: str,
        message_id: str,
    ) -> Optional[int]:
        """Remove the newest assistant chat row for ``message_id`` / ``message_pair_id``.

        Returns:
            Deleted ``seq`` or ``None`` when nothing matched.
        """

        mid = (message_id or "").strip()
        if not mid:
            return None
        with self._lock:
            row = self._connection.execute(
                """
                SELECT seq FROM run_messages
                WHERE run_id = ? AND tenant_id = ? AND kind = ?
                  AND (
                    json_extract(body_json, '$.message_id') = ?
                    OR json_extract(body_json, '$.message_pair_id') = ?
                  )
                ORDER BY seq DESC
                LIMIT 1
                """,
                (run_id, tenant_id, CHAT_KIND_ASSISTANT, mid, mid),
            ).fetchone()
            if row is None:
                return None
            seq = int(row["seq"])
            self._connection.execute(
                "DELETE FROM run_messages WHERE run_id = ? AND tenant_id = ? AND seq = ?",
                (run_id, tenant_id, seq),
            )
            self._connection.commit()
        return seq

    def fetch_chat_user_body_for_pair(
        self,
        *,
        run_id: str,
        tenant_id: str,
        message_pair_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Return the stored user chat ``body`` for ``message_pair_id``."""

        pid = (message_pair_id or "").strip()
        if not pid:
            return None
        with self._lock:
            urow = self._connection.execute(
                """
                SELECT body_json FROM run_messages
                WHERE run_id = ? AND tenant_id = ? AND kind = ?
                  AND json_extract(body_json, '$.message_pair_id') = ?
                ORDER BY seq ASC
                LIMIT 1
                """,
                (run_id, tenant_id, CHAT_KIND_USER, pid),
            ).fetchone()
        if urow is None:
            return None
        try:
            parsed = json.loads(urow["body_json"])
        except (TypeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class SqliteEvalRunStore:
    """Persisted release-gate eval summaries."""

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
                CREATE TABLE IF NOT EXISTS eval_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    eval_id TEXT NOT NULL UNIQUE,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    summary_json TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_eval_tenant_created ON eval_runs(tenant_id, id DESC)"
            )
            self._connection.commit()

    def insert(
        self,
        *,
        eval_id: str,
        tenant_id: str,
        user_id: str,
        passed: bool,
        summary: Dict[str, Any],
    ) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO eval_runs (eval_id, tenant_id, user_id, created_at, passed, summary_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    eval_id,
                    tenant_id,
                    user_id,
                    created_at,
                    1 if passed else 0,
                    json.dumps(summary, ensure_ascii=False),
                ),
            )
            self._connection.commit()

    def get(self, eval_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT eval_id, tenant_id, user_id, created_at, passed, summary_json
                FROM eval_runs WHERE eval_id = ?
                """,
                (eval_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "eval_id": row["eval_id"],
            "tenant_id": row["tenant_id"],
            "user_id": row["user_id"],
            "created_at": row["created_at"],
            "passed": bool(row["passed"]),
            "summary": json.loads(row["summary_json"]),
        }

    def list_page(
        self,
        *,
        tenant_id: str,
        after_id: int = 0,
        limit: int = 20,
    ) -> tuple[List[Dict[str, Any]], Optional[int]]:
        """List eval runs for tenant, id descending; after_id = last seen row id for pagination."""

        limit = max(1, min(100, limit))
        with self._lock:
            if after_id == 0:
                rows = self._connection.execute(
                    """
                    SELECT id, eval_id, tenant_id, user_id, created_at, passed, summary_json
                    FROM eval_runs
                    WHERE tenant_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (tenant_id, limit + 1),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT id, eval_id, tenant_id, user_id, created_at, passed, summary_json
                    FROM eval_runs
                    WHERE tenant_id = ? AND id < ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (tenant_id, after_id, limit + 1),
                ).fetchall()

        out: List[Dict[str, Any]] = []
        for row in rows[:limit]:
            out.append(
                {
                    "id": int(row["id"]),
                    "eval_id": row["eval_id"],
                    "tenant_id": row["tenant_id"],
                    "user_id": row["user_id"],
                    "created_at": row["created_at"],
                    "passed": bool(row["passed"]),
                    "summary": json.loads(row["summary_json"]),
                }
            )
        next_cursor: Optional[int] = None
        if len(rows) > limit and out:
            next_cursor = out[-1]["id"]
        return out, next_cursor

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class SqliteSessionCheckpointStore:
    """Session checkpoints (session_id aligns with run_id for MVP)."""

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
                CREATE TABLE IF NOT EXISTS session_checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, tenant_id, seq)
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_ck_sess ON session_checkpoints(session_id, tenant_id, seq)"
            )
            self._connection.commit()

    def append(
        self,
        *,
        session_id: str,
        tenant_id: str,
        label: str,
        payload: Dict[str, Any],
    ) -> int:
        """Persist a checkpoint; returns monotonic ``seq`` per (session_id, tenant_id)."""

        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COALESCE(MAX(seq), 0) AS m FROM session_checkpoints
                WHERE session_id = ? AND tenant_id = ?
                """,
                (session_id, tenant_id),
            ).fetchone()
            seq = int(row["m"]) + 1
            self._connection.execute(
                """
                INSERT INTO session_checkpoints (
                    session_id, tenant_id, seq, label, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    tenant_id,
                    seq,
                    label or "",
                    json.dumps(payload, ensure_ascii=False),
                    created_at,
                ),
            )
            self._connection.commit()
        return seq

    def list_for_session(self, *, session_id: str, tenant_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT seq, label, payload_json, created_at FROM session_checkpoints
                WHERE session_id = ? AND tenant_id = ?
                ORDER BY seq ASC
                """,
                (session_id, tenant_id),
            ).fetchall()
        return [
            {
                "seq": int(row["seq"]),
                "label": row["label"] or "",
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get(self, *, session_id: str, tenant_id: str, seq: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT seq, label, payload_json, created_at FROM session_checkpoints
                WHERE session_id = ? AND tenant_id = ? AND seq = ?
                """,
                (session_id, tenant_id, seq),
            ).fetchone()
        if row is None:
            return None
        return {
            "seq": int(row["seq"]),
            "label": row["label"] or "",
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()
