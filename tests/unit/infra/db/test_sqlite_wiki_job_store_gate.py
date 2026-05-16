"""Tests for wiki ingest job helpers used by wiki_search gating."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentium.infra.db.sqlite_wiki_job_store import SqliteWikiIngestJobStore


def test_verify_jobs_succeeded_requires_tenant_match(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    store = SqliteWikiIngestJobStore(db_path)
    jid = store.create_job(tenant_id="t1", blob_key="a.md", session_id="s1")
    ok, summaries = store.verify_jobs_succeeded_for_tenant("t1", [jid])
    assert ok is False
    assert summaries[0]["status"] == "queued"

    store.update_status(jid, status="succeeded")
    ok2, summaries2 = store.verify_jobs_succeeded_for_tenant("t1", [jid])
    assert ok2 is True
    assert summaries2[0]["status"] == "succeeded"

    ok3, summaries3 = store.verify_jobs_succeeded_for_tenant("t2", [jid])
    assert ok3 is False
    assert summaries3[0]["status"] == "wrong_tenant"


def test_list_recent_non_terminal_jobs_for_session_filters_age(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite"
    store = SqliteWikiIngestJobStore(db_path)

    jid_new = store.create_job(tenant_id="t1", blob_key="n.md", session_id="sess")
    jid_old = store.create_job(tenant_id="t1", blob_key="o.md", session_id="sess")

    conn = store._connect()
    try:
        old_created = (
            datetime.now(timezone.utc) - timedelta(hours=48)
        ).replace(microsecond=0).isoformat()
        conn.execute(
            "UPDATE wiki_ingest_jobs SET created_at = ?, updated_at = ? WHERE job_id = ?",
            (old_created, old_created, jid_old),
        )
        conn.commit()
    finally:
        conn.close()

    pending = store.list_recent_non_terminal_jobs_for_session(
        "t1",
        "sess",
        max_age_seconds=3600,
    )
    ids = {r.job_id for r in pending}
    assert jid_new in ids
    assert jid_old not in ids
