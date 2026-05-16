"""Tests for wiki DB read helpers (listing + exact get)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from crate.host_api import LlmWikiHost
from crate.stores.wiki_database import (
    SqliteWikiDatabase,
    WikiPageRecord,
    normalize_wiki_logical_path_key,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def test_list_summaries_filters_tenant(tmp_path: Path) -> None:
    db_path = tmp_path / "w.sqlite"
    wiki = SqliteWikiDatabase(db_path)

    wiki.upsert_page(
        WikiPageRecord(
            tenant_id="ta",
            logical_path="raw/a.md",
            body_md="a",
            content_sha256="1",
            updated_at=_now_iso(),
        )
    )
    wiki.upsert_page(
        WikiPageRecord(
            tenant_id="tb",
            logical_path="raw/b.md",
            body_md="b",
            content_sha256="2",
            updated_at=_now_iso(),
        )
    )

    rows = wiki.list_page_summaries("ta")
    paths = [r.logical_path for r in rows]
    assert paths == ["raw/a.md"]

    prefixed = wiki.list_page_summaries("ta", path_prefix="raw/")
    assert len(prefixed) == 1


def test_get_page_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "w.sqlite"
    wiki = SqliteWikiDatabase(db_path)

    wiki.upsert_page(
        WikiPageRecord(
            tenant_id="t1",
            logical_path="sessions/s1/raw/x.md",
            body_md="# Hi",
            content_sha256="9f",
            updated_at=_now_iso(),
        )
    )

    row = wiki.get_page("t1", "sessions/s1/raw/x.md")
    assert row is not None
    assert row.body_md == "# Hi"

    assert wiki.get_page("t1", "missing.md") is None


def test_host_list_and_get_delegate(tmp_path: Path) -> None:
    wiki = SqliteWikiDatabase(tmp_path / "wh.sqlite")
    host = LlmWikiHost(wiki_db=wiki)
    host.upsert_markdown_page(
        tenant_id="tx",
        logical_path="notes/p.md",
        body_md="text",
    )
    sums = host.list_page_summaries("tx")
    assert len(sums) == 1
    assert sums[0].logical_path == "notes/p.md"

    pg = host.get_page("tx", "notes/p.md")
    assert pg is not None
    assert "text" in pg.body_md


def test_normalize_rejects_traversal() -> None:
    with pytest.raises(ValueError):
        normalize_wiki_logical_path_key("../evil")
