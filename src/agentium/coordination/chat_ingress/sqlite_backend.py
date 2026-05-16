"""SQLite ingress backend: single-node multi-process (WAL + busy_timeout)."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from agentium.coordination.chat_ingress.types import CollectAppendResult


class SqliteChatIngressBackend:
    """Exclusive short transactions for lease + queue rows."""

    def __init__(self, *, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=30.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS ingress_lease (
                  session_key TEXT PRIMARY KEY,
                  token TEXT NOT NULL,
                  expires_real REAL NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS ingress_followup (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  session_key TEXT NOT NULL,
                  payload TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS ingress_steer (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  session_key TEXT NOT NULL,
                  text TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS ingress_collect (
                  session_key TEXT PRIMARY KEY,
                  fragments_json TEXT NOT NULL,
                  deadline_real REAL NOT NULL
                )
                """
            )

    def has_lease(self, session_key: str) -> bool:
        now = time.time()
        with self._lock, self._connect() as c:
            row = c.execute(
                "SELECT expires_real FROM ingress_lease WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            if row is None:
                return False
            if float(row[0]) < now:
                c.execute("DELETE FROM ingress_lease WHERE session_key = ?", (session_key,))
                return False
            return True

    def try_acquire_lease(self, session_key: str, token: str, ttl_sec: float) -> bool:
        now = time.time()
        exp = now + ttl_sec
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT token, expires_real FROM ingress_lease WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            if row is not None:
                if float(row[1]) < now:
                    c.execute("DELETE FROM ingress_lease WHERE session_key = ?", (session_key,))
                elif str(row[0]) == token:
                    c.execute(
                        "UPDATE ingress_lease SET expires_real = ? WHERE session_key = ?",
                        (exp, session_key),
                    )
                    c.execute("COMMIT")
                    return True
                else:
                    c.execute("ROLLBACK")
                    return False
            c.execute(
                "INSERT INTO ingress_lease(session_key, token, expires_real) VALUES (?,?,?)",
                (session_key, token, exp),
            )
            c.execute("COMMIT")
            return True

    def renew_lease(self, session_key: str, token: str, ttl_sec: float) -> bool:
        now = time.time()
        exp = now + ttl_sec
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT token, expires_real FROM ingress_lease WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            if row is None or str(row[0]) != token:
                c.execute("ROLLBACK")
                return False
            if float(row[1]) < now:
                c.execute("DELETE FROM ingress_lease WHERE session_key = ?", (session_key,))
                c.execute("ROLLBACK")
                return False
            c.execute(
                "UPDATE ingress_lease SET expires_real = ? WHERE session_key = ?",
                (exp, session_key),
            )
            c.execute("COMMIT")
            return True

    def release_lease(self, session_key: str, token: str) -> None:
        with self._lock, self._connect() as c:
            c.execute(
                "DELETE FROM ingress_lease WHERE session_key = ? AND token = ?",
                (session_key, token),
            )

    def followup_depth(self, session_key: str) -> int:
        with self._lock, self._connect() as c:
            row = c.execute(
                "SELECT COUNT(*) FROM ingress_followup WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            return int(row[0]) if row else 0

    def followup_enqueue(self, session_key: str, payload_json: str) -> int:
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO ingress_followup(session_key, payload) VALUES (?,?)",
                (session_key, payload_json),
            )
            row = c.execute(
                "SELECT COUNT(*) FROM ingress_followup WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            return int(row[0]) if row else 0

    def followup_pop(self, session_key: str) -> Optional[str]:
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT id, payload FROM ingress_followup WHERE session_key = ? ORDER BY id LIMIT 1",
                (session_key,),
            ).fetchone()
            if row is None:
                c.execute("ROLLBACK")
                return None
            c.execute("DELETE FROM ingress_followup WHERE id = ?", (row[0],))
            c.execute("COMMIT")
            return str(row[1])

    def steer_append(self, session_key: str, text: str) -> None:
        t = (text or "").strip()
        if not t:
            return
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO ingress_steer(session_key, text) VALUES (?,?)",
                (session_key, t),
            )

    def steer_drain(self, session_key: str) -> str:
        with self._lock, self._connect() as c:
            rows = c.execute(
                "SELECT text FROM ingress_steer WHERE session_key = ? ORDER BY id",
                (session_key,),
            ).fetchall()
            c.execute("DELETE FROM ingress_steer WHERE session_key = ?", (session_key,))
        parts = [str(r[0]) for r in rows]
        return "\n\n".join(parts) if parts else ""

    def collect_append(
        self,
        session_key: str,
        fragment: str,
        debounce_ms: int,
        cap: int,
        on_flush: Optional[Callable[[str, str], None]] = None,
    ) -> CollectAppendResult:
        _ = on_flush
        import json as _json

        frag = (fragment or "").strip()
        if not frag:
            return CollectAppendResult(False, 0, None, None)
        now = time.time()
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT fragments_json, deadline_real FROM ingress_collect WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            frags: List[str]
            if row:
                raw = _json.loads(str(row[0]))
                frags = [str(x) for x in raw] if isinstance(raw, list) else []
            else:
                frags = []
            frags.append(frag)
            if len(frags) > cap:
                frags = frags[-cap:]
            depth = len(frags)

            if debounce_ms <= 0:
                merged = "\n\n".join(frags)
                c.execute("DELETE FROM ingress_collect WHERE session_key = ?", (session_key,))
                c.execute("COMMIT")
                return CollectAppendResult(False, depth, None, merged)

            dl = now + debounce_ms / 1000.0
            c.execute(
                """
                INSERT INTO ingress_collect(session_key, fragments_json, deadline_real)
                VALUES (?,?,?)
                ON CONFLICT(session_key) DO UPDATE SET
                  fragments_json=excluded.fragments_json,
                  deadline_real=excluded.deadline_real
                """,
                (session_key, _json.dumps(frags), dl),
            )
            c.execute("COMMIT")
        return CollectAppendResult(True, depth, debounce_ms, None)

    def collect_peek_merged_if_ready(self, session_key: str, debounce_ms: int) -> Optional[str]:
        _ = debounce_ms
        import json as _json

        now = time.time()
        with self._lock, self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT fragments_json, deadline_real FROM ingress_collect WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            if row is None:
                c.execute("ROLLBACK")
                return None
            if float(row[1]) > now:
                c.execute("ROLLBACK")
                return None
            raw = _json.loads(str(row[0]))
            frags = [str(x) for x in raw] if isinstance(raw, list) else []
            merged = "\n\n".join(frags)
            c.execute("DELETE FROM ingress_collect WHERE session_key = ?", (session_key,))
            c.execute("COMMIT")
            return merged or None
