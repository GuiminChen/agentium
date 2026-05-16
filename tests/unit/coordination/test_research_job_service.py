"""Research job service (P1-24)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.app.settings import load_settings
from agentium.coordination.research_job import ResearchJobService
from agentium.coordination.task_lock.sqlite_backend import SqliteTaskLockBackend
from agentium.models.harness_contract import HarnessContract


def test_research_job_create_and_get() -> None:
    svc = ResearchJobService()
    rec = svc.create_job(tenant_id="ta", query="hello world", max_workers=3)
    assert rec.status == "running"
    got = svc.get(tenant_id="ta", job_id=rec.job_id)
    assert got is not None
    assert got.job_id == rec.job_id


def test_research_job_tenant_isolation() -> None:
    svc = ResearchJobService()
    rec = svc.create_job(tenant_id="ta", query="q", max_workers=2)
    assert svc.get(tenant_id="other", job_id=rec.job_id) is None


def test_research_job_task_lock_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.setenv("AGENTIUM_FEATURE_TASK_LOCK", "1")
    settings = load_settings()
    be = SqliteTaskLockBackend(path=tmp_path / "tl.db")
    holder_svc = ResearchJobService(settings=settings, task_lock_backend=be)
    harness = HarnessContract(lock_resource_keys=["exclusive"])
    first = holder_svc.create_job(
        tenant_id="t", query="a", max_workers=2, harness=harness
    )
    assert first.status == "running"
    second = holder_svc.create_job(
        tenant_id="t", query="b", max_workers=2, harness=harness
    )
    assert second.status == "blocked"
    assert second.phase == "task_lock_denied"
