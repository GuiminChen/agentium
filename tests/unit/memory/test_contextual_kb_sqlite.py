"""SQLite contextual KB store tests (P1-5)."""

from __future__ import annotations

from pathlib import Path

from agentium.memory.contextual_retrieval import SqliteContextualKbStore


def test_kb_fts_search_hits(tmp_path: Path) -> None:
    db = tmp_path / "kb.db"
    store = SqliteContextualKbStore(db)
    store.index_chunk(
        tenant_id="t1",
        doc_id="d1",
        chunk_id="c1",
        contextual_prefix="WidgetCo knowledge base",
        body="Refunds require order id and receipt within 30 days.",
    )
    hits = store.search(tenant_id="t1", query="refund receipt", top_k=5)
    assert len(hits) >= 1
    assert hits[0]["doc_id"] == "d1"
