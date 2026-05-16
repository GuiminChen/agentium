"""PostgreSQL-backed ingress (optional dependency: psycopg)."""

from __future__ import annotations

import json
import threading
import time
from typing import Callable, List, Optional

from agentium.coordination.chat_ingress.types import CollectAppendResult


class PostgresChatIngressBackend:
    """Short transactions with session_key PK; uses psycopg v3."""

    def __init__(self, *, url: str) -> None:
        stripped = (url or "").strip()
        if not stripped:
            raise ValueError("PostgreSQL chat ingress requires AGENTIUM_CHAT_INGRESS_DATABASE_URL.")
        self._url = stripped
        self._lock = threading.Lock()
        try:
            import psycopg  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "PostgreSQL chat ingress requires psycopg (pip install 'psycopg[binary]')."
            ) from exc
        self._psycopg = psycopg
        self._init_db()

    def _connect(self):
        return self._psycopg.connect(self._url)

    def _init_db(self) -> None:
        with self._connect() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_ingress_lease (
                  session_key TEXT PRIMARY KEY,
                  token TEXT NOT NULL,
                  expires_real DOUBLE PRECISION NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_ingress_followup (
                  id BIGSERIAL PRIMARY KEY,
                  session_key TEXT NOT NULL,
                  payload TEXT NOT NULL
                )
                """
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_followup_sk ON chat_ingress_followup(session_key)"
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_ingress_steer (
                  id BIGSERIAL PRIMARY KEY,
                  session_key TEXT NOT NULL,
                  text TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_ingress_collect (
                  session_key TEXT PRIMARY KEY,
                  fragments_json TEXT NOT NULL,
                  deadline_real DOUBLE PRECISION NOT NULL
                )
                """
            )

    def has_lease(self, session_key: str) -> bool:
        now = time.time()
        with self._lock, self._connect() as c:
            row = c.execute(
                "SELECT expires_real FROM chat_ingress_lease WHERE session_key = %s",
                (session_key,),
            ).fetchone()
            if row is None:
                return False
            if float(row[0]) < now:
                c.execute("DELETE FROM chat_ingress_lease WHERE session_key = %s", (session_key,))
                return False
            return True

    def try_acquire_lease(self, session_key: str, token: str, ttl_sec: float) -> bool:
        now = time.time()
        exp = now + ttl_sec
        with self._lock, self._connect() as c:
            row = c.execute(
                "SELECT token, expires_real FROM chat_ingress_lease WHERE session_key = %s",
                (session_key,),
            ).fetchone()
            if row is not None:
                if float(row[1]) < now:
                    c.execute("DELETE FROM chat_ingress_lease WHERE session_key = %s", (session_key,))
                elif str(row[0]) == token:
                    c.execute(
                        "UPDATE chat_ingress_lease SET expires_real = %s WHERE session_key = %s",
                        (exp, session_key),
                    )
                    return True
                else:
                    return False
            c.execute(
                "INSERT INTO chat_ingress_lease(session_key, token, expires_real) VALUES (%s,%s,%s)",
                (session_key, token, exp),
            )
            return True

    def renew_lease(self, session_key: str, token: str, ttl_sec: float) -> bool:
        now = time.time()
        exp = now + ttl_sec
        with self._lock, self._connect() as c:
            row = c.execute(
                "SELECT token, expires_real FROM chat_ingress_lease WHERE session_key = %s",
                (session_key,),
            ).fetchone()
            if row is None or str(row[0]) != token:
                return False
            if float(row[1]) < now:
                c.execute("DELETE FROM chat_ingress_lease WHERE session_key = %s", (session_key,))
                return False
            c.execute(
                "UPDATE chat_ingress_lease SET expires_real = %s WHERE session_key = %s",
                (exp, session_key),
            )
            return True

    def release_lease(self, session_key: str, token: str) -> None:
        with self._lock, self._connect() as c:
            c.execute(
                "DELETE FROM chat_ingress_lease WHERE session_key = %s AND token = %s",
                (session_key, token),
            )

    def followup_depth(self, session_key: str) -> int:
        with self._lock, self._connect() as c:
            row = c.execute(
                "SELECT COUNT(*) FROM chat_ingress_followup WHERE session_key = %s",
                (session_key,),
            ).fetchone()
            return int(row[0]) if row else 0

    def followup_enqueue(self, session_key: str, payload_json: str) -> int:
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO chat_ingress_followup(session_key, payload) VALUES (%s,%s)",
                (session_key, payload_json),
            )
            row = c.execute(
                "SELECT COUNT(*) FROM chat_ingress_followup WHERE session_key = %s",
                (session_key,),
            ).fetchone()
            return int(row[0]) if row else 0

    def followup_pop(self, session_key: str) -> Optional[str]:
        with self._lock, self._connect() as c:
            row = c.execute(
                """
                SELECT id, payload FROM chat_ingress_followup
                WHERE session_key = %s ORDER BY id ASC LIMIT 1 FOR UPDATE SKIP LOCKED
                """,
                (session_key,),
            ).fetchone()
            if row is None:
                return None
            c.execute("DELETE FROM chat_ingress_followup WHERE id = %s", (row[0],))
            return str(row[1])

    def steer_append(self, session_key: str, text: str) -> None:
        t = (text or "").strip()
        if not t:
            return
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO chat_ingress_steer(session_key, text) VALUES (%s,%s)",
                (session_key, t),
            )

    def steer_drain(self, session_key: str) -> str:
        with self._lock, self._connect() as c:
            rows = c.execute(
                "SELECT text FROM chat_ingress_steer WHERE session_key = %s ORDER BY id",
                (session_key,),
            ).fetchall()
            c.execute("DELETE FROM chat_ingress_steer WHERE session_key = %s", (session_key,))
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
        frag = (fragment or "").strip()
        if not frag:
            return CollectAppendResult(False, 0, None, None)
        now = time.time()
        with self._lock, self._connect() as c:
            row = c.execute(
                "SELECT fragments_json, deadline_real FROM chat_ingress_collect WHERE session_key = %s",
                (session_key,),
            ).fetchone()
            frags: List[str]
            if row:
                raw = json.loads(str(row[0]))
                frags = [str(x) for x in raw] if isinstance(raw, list) else []
            else:
                frags = []
            frags.append(frag)
            if len(frags) > cap:
                frags = frags[-cap:]
            depth = len(frags)
            if debounce_ms <= 0:
                merged = "\n\n".join(frags)
                c.execute("DELETE FROM chat_ingress_collect WHERE session_key = %s", (session_key,))
                return CollectAppendResult(False, depth, None, merged)

            dl = now + debounce_ms / 1000.0
            c.execute(
                """
                INSERT INTO chat_ingress_collect(session_key, fragments_json, deadline_real)
                VALUES (%s,%s,%s)
                ON CONFLICT (session_key) DO UPDATE SET
                  fragments_json = EXCLUDED.fragments_json,
                  deadline_real = EXCLUDED.deadline_real
                """,
                (session_key, json.dumps(frags), dl),
            )
        return CollectAppendResult(True, depth, debounce_ms, None)

    def collect_peek_merged_if_ready(self, session_key: str, debounce_ms: int) -> Optional[str]:
        _ = debounce_ms
        now = time.time()
        with self._lock, self._connect() as c:
            row = c.execute(
                "SELECT fragments_json, deadline_real FROM chat_ingress_collect WHERE session_key = %s",
                (session_key,),
            ).fetchone()
            if row is None or float(row[1]) > now:
                return None
            raw = json.loads(str(row[0]))
            frags = [str(x) for x in raw] if isinstance(raw, list) else []
            merged = "\n\n".join(frags)
            c.execute("DELETE FROM chat_ingress_collect WHERE session_key = %s", (session_key,))
            return merged or None
