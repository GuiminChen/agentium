"""SQLite FTS5-backed contextual KB chunks (P1-5 MVP)."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List


def _fts_match_query(raw: str) -> str:
    """Build a broad-recall OR query for FTS5 from free text."""

    parts = re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", raw.lower())
    if not parts:
        return ""
    # OR for MVP recall; callers still apply tenant isolation in SQL.
    return " OR ".join(parts)


class SqliteContextualKbStore:
    """Tenant-isolated KB chunks with FTS5 retrieval + stub vector score hook."""

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path).expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
                  tenant_id UNINDEXED,
                  doc_id UNINDEXED,
                  chunk_id UNINDEXED,
                  body,
                  contextual_prefix,
                  tokenize = 'porter unicode61'
                );
                """
            )

    def index_chunk(
        self,
        *,
        tenant_id: str,
        doc_id: str,
        chunk_id: str,
        body: str,
        contextual_prefix: str = "",
    ) -> None:
        """Insert one chunk row (append-only; duplicate chunk_id allowed for MVP)."""

        tid = str(tenant_id).strip()
        if not tid:
            raise ValueError("tenant_id required")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kb_fts(tenant_id, doc_id, chunk_id, body, contextual_prefix)
                VALUES (?, ?, ?, ?, ?)
                """,
                (tid, str(doc_id).strip(), str(chunk_id).strip(), body, contextual_prefix),
            )
            conn.commit()

    @staticmethod
    def _stub_score(query: str, body: str, ctx: str) -> float:
        from agentium.tools.tool_search_index import score_query_against_text

        return score_query_against_text(query, f"{ctx} {body}")

    def search(
        self,
        *,
        tenant_id: str,
        query: str,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """FTS retrieve + stub secondary score (for hybrid hook demonstrations)."""

        tid = str(tenant_id).strip()
        if not tid:
            return []
        q = _fts_match_query(query)
        cap = max(1, min(64, int(top_k)))
        if not q:
            return []
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    """
                    SELECT doc_id, chunk_id, body, contextual_prefix, bm25(kb_fts) AS rnk
                    FROM kb_fts
                    WHERE tenant_id = ? AND kb_fts MATCH ?
                    ORDER BY rnk
                    LIMIT ?
                    """,
                    (tid, q, cap),
                )
            except sqlite3.OperationalError:
                cur = conn.execute(
                    """
                    SELECT doc_id, chunk_id, body, contextual_prefix, 0.0 AS rnk
                    FROM kb_fts
                    WHERE tenant_id = ? AND kb_fts MATCH ?
                    LIMIT ?
                    """,
                    (tid, q, cap),
                )
            rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            body = str(row["body"])
            cpre = str(row["contextual_prefix"] or "")
            stub = self._stub_score(query, body, cpre)
            out.append(
                {
                    "doc_id": str(row["doc_id"]),
                    "chunk_id": str(row["chunk_id"]),
                    "body": body,
                    "contextual_prefix": cpre,
                    "fts_rank": float(row["rnk"]),
                    "stub_vec_score": stub,
                }
            )
        out.sort(key=lambda x: (-float(x["stub_vec_score"]), float(x["fts_rank"])))
        return out[:cap]
