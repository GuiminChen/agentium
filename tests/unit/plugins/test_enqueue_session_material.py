"""Unit tests for ``LlmWikiPluginService.enqueue_session_material``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("crate.stores.wiki_paths")

from agentium.app.plugins_config import LlmWikiPluginConfig
from agentium.infra.db.sqlite_wiki_job_store import SqliteWikiIngestJobStore
from agentium.plugins.llm_wiki.service import LlmWikiPluginService


def test_enqueue_session_material_put_and_enqueue(tmp_path: Path) -> None:
    blobs = MagicMock()
    blobs.put = MagicMock(return_value="etag")

    store = SqliteWikiIngestJobStore(tmp_path / "wiki_jobs.sqlite")
    cfg = LlmWikiPluginConfig(enabled=True, session_upload_max_decoded_bytes=4096)
    settings = MagicMock()
    settings.data_dir = tmp_path

    svc = LlmWikiPluginService(
        settings=settings,
        cfg=cfg,
        job_store=store,
        host=MagicMock(),
        blobs=blobs,
        wiki_db=MagicMock(),
    )
    sink = MagicMock()

    blob_key, jid = svc.enqueue_session_material(
        tenant_id="t1",
        session_id="sess-a",
        filename="hello.md",
        raw_bytes=b"# hello wiki",
        deferred_sink=sink,
    )

    assert blob_key.startswith("workspace/")
    assert blob_key.endswith(".md")
    assert jid
    blobs.put.assert_called_once_with("t1", blob_key, b"# hello wiki")
    sink.enqueue.assert_called_once()

    rec = store.get_job(jid)
    assert rec is not None
    assert rec.tenant_id == "t1"
    assert rec.session_id == "sess-a"
    assert rec.blob_key == blob_key


def test_enqueue_session_material_empty_bytes(tmp_path: Path) -> None:
    from agentium.plugins.llm_wiki.session_material import SessionMaterialUploadError

    blobs = MagicMock()
    store = SqliteWikiIngestJobStore(tmp_path / "wiki_jobs.sqlite")
    cfg = LlmWikiPluginConfig(enabled=True)
    settings = MagicMock()
    settings.data_dir = tmp_path
    svc = LlmWikiPluginService(
        settings=settings,
        cfg=cfg,
        job_store=store,
        host=MagicMock(),
        blobs=blobs,
        wiki_db=MagicMock(),
    )
    with pytest.raises(SessionMaterialUploadError) as ei:
        svc.enqueue_session_material(
            tenant_id="t1",
            session_id="sess-a",
            filename="x.md",
            raw_bytes=b"",
            deferred_sink=MagicMock(),
        )
    assert ei.value.code == "empty_payload"
    blobs.put.assert_not_called()
