"""Tests for SqliteChatSessionStore.merge_session_metadata."""

from __future__ import annotations

from pathlib import Path

from agentium.infra.db.sqlite_chat_session_store import SqliteChatSessionStore


def test_merge_session_metadata_shallow_updates(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    store = SqliteChatSessionStore(db)
    try:
        store.create_session(
            tenant_id="t1",
            session_id="s1",
            title=None,
            skill="sk",
            intro_text=None,
            metadata={"a": 1, "b": 2},
        )
        rec = store.merge_session_metadata(
            tenant_id="t1",
            session_id="s1",
            patch={"b": 3, "c": 4},
        )
        assert rec.metadata == {"a": 1, "b": 3, "c": 4}
        assert rec.skill == "sk"
    finally:
        store.close()


def test_merge_session_metadata_removes_with_none(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    store = SqliteChatSessionStore(db)
    try:
        store.create_session(
            tenant_id="t1",
            session_id="s1",
            title=None,
            skill=None,
            intro_text=None,
            metadata={"keep": 1, "drop": 2},
        )
        rec = store.merge_session_metadata(
            tenant_id="t1",
            session_id="s1",
            patch={"drop": None},
        )
        assert rec.metadata == {"keep": 1}
        assert "drop" not in rec.metadata
    finally:
        store.close()
