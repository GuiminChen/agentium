"""SQLite scheduled job store unit tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agentium.infra.db.sqlite_scheduled_job_store import (
    SqliteScheduledJobStore,
    compute_initial_next_run_at_unix_ms,
)


def test_compute_initial_next_interval(tmp_path: Path) -> None:
    del tmp_path
    trig = {"kind": "interval", "interval_seconds": 120}
    assert compute_initial_next_run_at_unix_ms(trig, 1_000_000) == 1_000_000 + 120_000


def test_compute_initial_one_shot(tmp_path: Path) -> None:
    del tmp_path
    trig = {"kind": "one_shot", "run_at_unix_ms": 42}
    assert compute_initial_next_run_at_unix_ms(trig, 99) == 42


def test_compute_initial_cron_hourly(tmp_path: Path) -> None:
    pytest.importorskip("croniter")
    del tmp_path
    trig = {"kind": "cron", "cron_expression": "0 * * * *"}
    out = compute_initial_next_run_at_unix_ms(trig, 1_700_000_000_000)
    assert isinstance(out, int)
    assert out > 1_700_000_000_000


def test_try_claim_advances_interval_and_updates_next_run(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    store = SqliteScheduledJobStore(db_path)
    now = 10_000_000
    row = store.insert_job(
        tenant_id="t1",
        user_id="u1",
        name="n",
        enabled=True,
        task_kind="chat_turn",
        trigger={"kind": "interval", "interval_seconds": 60},
        session_binding="named_persistent",
        pinned_session_id=None,
        payload={"message_content": "hi"},
        policy_bundle_ref=None,
        budget_estimate_tokens=None,
        max_retries=0,
        timeout_seconds=120.0,
        next_run_at_unix_ms=now - 1000,
    )
    claimed = store.try_claim_due_job(now_ms=now)
    assert claimed is not None
    assert claimed.job_id == row.job_id
    updated = store.get_job(job_id=row.job_id, tenant_id="t1")
    assert updated is not None
    assert updated.next_run_at_unix_ms == now + 60_000
    store.close()


def test_list_runs_started_after_before(tmp_path: Path) -> None:
    db_path = tmp_path / "runs_filter.db"
    store = SqliteScheduledJobStore(db_path)
    row = store.insert_job(
        tenant_id="t1",
        user_id="u1",
        name="x",
        enabled=False,
        task_kind="chat_turn",
        trigger={"kind": "one_shot", "run_at_unix_ms": 100},
        session_binding="named_persistent",
        pinned_session_id=None,
        payload={"message_content": "x"},
        policy_bundle_ref=None,
        budget_estimate_tokens=None,
        max_retries=0,
        timeout_seconds=120.0,
        next_run_at_unix_ms=None,
    )
    for rid, ts in (
        ("r_early", "2024-01-02T12:00:00+00:00"),
        ("r_late", "2024-01-05T12:00:00+00:00"),
    ):
        store.insert_run(
            run_id=rid,
            job_id=row.job_id,
            tenant_id="t1",
            status="running",
            attempt_no=1,
            trace_id=f"t-{rid}",
            session_id="s1",
        )
        store.finish_run(run_id=rid, status="succeeded", error_detail=None)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE scheduled_job_runs SET started_at = ? WHERE run_id = ?",
            (ts, rid),
        )
        conn.commit()
        conn.close()

    items, total = store.list_runs(
        tenant_id="t1",
        job_id=row.job_id,
        page=1,
        page_size=10,
        started_after="2024-01-03T00:00:00+00:00",
    )
    assert total == 1
    assert items[0].run_id == "r_late"

    items2, total2 = store.list_runs(
        tenant_id="t1",
        job_id=row.job_id,
        page=1,
        page_size=10,
        started_before="2024-01-03T00:00:00+00:00",
    )
    assert total2 == 1
    assert items2[0].run_id == "r_early"
    store.close()


def test_list_runs_after_insert(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs2.db"
    store = SqliteScheduledJobStore(db_path)
    row = store.insert_job(
        tenant_id="t1",
        user_id="u1",
        name="x",
        enabled=False,
        task_kind="chat_turn",
        trigger={"kind": "one_shot", "run_at_unix_ms": 100},
        session_binding="named_persistent",
        pinned_session_id=None,
        payload={"message_content": "x"},
        policy_bundle_ref=None,
        budget_estimate_tokens=None,
        max_retries=0,
        timeout_seconds=120.0,
        next_run_at_unix_ms=None,
    )
    store.insert_run(
        run_id="r1",
        job_id=row.job_id,
        tenant_id="t1",
        status="running",
        attempt_no=1,
        trace_id="tr",
        session_id="s1",
    )
    store.finish_run(run_id="r1", status="succeeded", error_detail=None)
    items, total = store.list_runs(tenant_id="t1", job_id=row.job_id, page=1, page_size=10)
    assert total == 1
    assert items[0].status == "succeeded"
    store.close()


def test_claim_webhook_idempotency_key(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs-wh.db"
    store = SqliteScheduledJobStore(db_path)
    assert store.claim_webhook_idempotency_key(
        tenant_id="t1",
        job_id="j1",
        idempotency_key=" abc ",
        received_unix_ms=100,
    )
    assert not store.claim_webhook_idempotency_key(
        tenant_id="t1",
        job_id="j1",
        idempotency_key="abc",
        received_unix_ms=200,
    )
    assert store.claim_webhook_idempotency_key(
        tenant_id="t1",
        job_id="j2",
        idempotency_key="abc",
        received_unix_ms=300,
    )
    assert store.claim_webhook_idempotency_key(
        tenant_id="t1",
        job_id="j1",
        idempotency_key="",
        received_unix_ms=400,
    )
    store.close()
