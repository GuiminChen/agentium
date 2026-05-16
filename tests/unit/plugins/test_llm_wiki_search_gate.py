"""Tests for ``wiki_search`` admission gate."""

from __future__ import annotations

from pathlib import Path

from agentium.app.plugins_config import LlmWikiPluginConfig
from agentium.infra.db.sqlite_wiki_job_store import SqliteWikiIngestJobStore
from agentium.plugins.llm_wiki.search_gate import wiki_search_gate_violation


def test_gate_blocks_until_wait_for_jobs_succeed(tmp_path: Path) -> None:
    store = SqliteWikiIngestJobStore(tmp_path / "j.sqlite")
    jid = store.create_job(tenant_id="ta", blob_key="x.md", session_id="s1")
    cfg = LlmWikiPluginConfig(
        wiki_search_block_session_when_jobs_pending=False,
    )
    viol = wiki_search_gate_violation(
        store,
        cfg,
        tenant_id="ta",
        scope="tenant",
        session_id="",
        wait_for_job_ids=[jid],
    )
    assert viol is not None
    assert viol["code"] == "wiki_wait_for_jobs_not_ready"

    store.update_status(jid, status="succeeded")
    viol2 = wiki_search_gate_violation(
        store,
        cfg,
        tenant_id="ta",
        scope="tenant",
        session_id="",
        wait_for_job_ids=[jid],
    )
    assert viol2 is None


def test_gate_blocks_session_when_pending_jobs_within_ttl(tmp_path: Path) -> None:
    store = SqliteWikiIngestJobStore(tmp_path / "j.sqlite")
    store.create_job(tenant_id="ta", blob_key="x.md", session_id="sx")
    cfg = LlmWikiPluginConfig(wiki_search_block_session_when_jobs_pending=True)
    viol = wiki_search_gate_violation(
        store,
        cfg,
        tenant_id="ta",
        scope="session",
        session_id="sx",
        wait_for_job_ids=[],
    )
    assert viol is not None
    assert viol["code"] == "wiki_session_jobs_pending"

    cfg_off = LlmWikiPluginConfig(wiki_search_block_session_when_jobs_pending=False)
    viol2 = wiki_search_gate_violation(
        store,
        cfg_off,
        tenant_id="ta",
        scope="session",
        session_id="sx",
        wait_for_job_ids=[],
    )
    assert viol2 is None


def test_gate_tenant_scope_ignores_session_pending(tmp_path: Path) -> None:
    store = SqliteWikiIngestJobStore(tmp_path / "j.sqlite")
    store.create_job(tenant_id="ta", blob_key="x.md", session_id="sx")
    cfg = LlmWikiPluginConfig(wiki_search_block_session_when_jobs_pending=True)
    viol = wiki_search_gate_violation(
        store,
        cfg,
        tenant_id="ta",
        scope="tenant",
        session_id="",
        wait_for_job_ids=[],
    )
    assert viol is None
