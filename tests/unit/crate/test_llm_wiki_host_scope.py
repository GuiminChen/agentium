"""Tests for LlmWikiHost.search session vs tenant scope."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from crate.host_api import LlmWikiHost
from crate.stores.wiki_database import SqliteWikiDatabase, WikiPageRecord
from crate.stores.wiki_paths import wiki_logical_path_for_blob


def test_host_search_session_filters_by_path_prefix(tmp_path: Path) -> None:
    db = SqliteWikiDatabase(tmp_path / "wiki.sqlite")
    host = LlmWikiHost(wiki_db=db)
    tenant_id = "tenant-a"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def upsert(logical_path: str, body: str) -> None:
        sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        db.upsert_page(
            WikiPageRecord(
                tenant_id=tenant_id,
                logical_path=logical_path,
                body_md=body,
                content_sha256=sha,
                updated_at=now,
            )
        )

    upsert(
        wiki_logical_path_for_blob(blob_key="a.md", session_id="s1"),
        "hello session one",
    )
    upsert(
        wiki_logical_path_for_blob(blob_key="b.md", session_id="s2"),
        "hello session two",
    )
    upsert(wiki_logical_path_for_blob(blob_key="c.md"), "hello global")

    scoped = host.search(
        tenant_id, "hello", literal=True, scope="session", chat_session_id="s1"
    )
    assert len(scoped["literals"]) == 1
    assert scoped["literals"][0]["logical_path"].startswith("sessions/s1/")

    full = host.search(tenant_id, "hello", literal=True, scope="tenant")
    assert len(full["literals"]) == 3

    missing_sid = host.search(
        tenant_id, "hello", literal=True, scope="session", chat_session_id=""
    )
    assert missing_sid["literals"] == []
    assert (
        missing_sid["search_meta"].get("hint") == "session_scope_requires_session_id"
    )
