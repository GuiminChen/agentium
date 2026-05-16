"""Harness coordination hooks (P2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.app.settings import load_settings
from agentium.coordination.harness_task_coordination import acquire_harness_locks
from agentium.coordination.task_lock.sqlite_backend import SqliteTaskLockBackend
from agentium.models.harness_contract import HarnessContract


def test_acquire_harness_locks_rollback_second_key_denied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.setenv("AGENTIUM_FEATURE_TASK_LOCK", "1")
    settings = load_settings()
    be = SqliteTaskLockBackend(path=tmp_path / "x.db")
    assert (
        be.try_acquire(
            tenant_id="t",
            resource_key="a",
            holder_run_id="other",
            ttl_seconds=120.0,
        )
        is not None
    )
    contract = HarnessContract(lock_resource_keys=["a", "b"])
    ok, leases = acquire_harness_locks(
        backend=be,
        settings=settings,
        tenant_id="t",
        holder_run_id="me",
        contract=contract,
    )
    assert ok is False
    assert leases == []
    intruder = be.try_acquire(
        tenant_id="t",
        resource_key="a",
        holder_run_id="intruder",
        ttl_seconds=120.0,
    )
    assert intruder is None


def test_run_minimal_oracle_respects_feature_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.setenv("AGENTIUM_HARNESS_ORACLE_ENABLED", "0")
    from agentium.coordination.harness_task_coordination import run_minimal_oracle_if_configured

    settings = load_settings()
    contract = HarnessContract(oracle_command_ref="builtin:token_ok")
    run_minimal_oracle_if_configured(
        settings=settings,
        contract=contract,
        job_id="j1",
        tenant_id="t1",
    )
