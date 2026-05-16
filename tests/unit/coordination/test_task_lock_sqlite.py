"""SQLite task lock backend (P2)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from agentium.coordination.task_lock.sqlite_backend import SqliteTaskLockBackend


def test_task_lock_single_acquire_release(tmp_path: Path) -> None:
    path = tmp_path / "tl.db"
    be = SqliteTaskLockBackend(path=path)
    lease = be.try_acquire(
        tenant_id="t1",
        resource_key="repo:main",
        holder_run_id="run-a",
        ttl_seconds=60.0,
    )
    assert lease is not None
    denied = be.try_acquire(
        tenant_id="t1",
        resource_key="repo:main",
        holder_run_id="run-b",
        ttl_seconds=60.0,
    )
    assert denied is None
    assert be.release(tenant_id="t1", resource_key="repo:main", holder_run_id="run-a") is True
    again = be.try_acquire(
        tenant_id="t1",
        resource_key="repo:main",
        holder_run_id="run-b",
        ttl_seconds=60.0,
    )
    assert again is not None


def test_task_lock_ttl_allows_next_holder(tmp_path: Path) -> None:
    path = tmp_path / "tl2.db"
    be = SqliteTaskLockBackend(path=path)
    assert (
        be.try_acquire(
            tenant_id="t1",
            resource_key="k",
            holder_run_id="a",
            ttl_seconds=0.05,
        )
        is not None
    )
    time.sleep(0.12)
    nxt = be.try_acquire(
        tenant_id="t1",
        resource_key="k",
        holder_run_id="b",
        ttl_seconds=60.0,
    )
    assert nxt is not None


def test_task_lock_concurrent_single_winner(tmp_path: Path) -> None:
    path = tmp_path / "tl3.db"
    be = SqliteTaskLockBackend(path=path)
    barrier = threading.Barrier(4)
    successes: list[bool] = []
    lock = threading.Lock()

    def worker(holder: str) -> None:
        barrier.wait()
        got = be.try_acquire(
            tenant_id="t",
            resource_key="shared",
            holder_run_id=holder,
            ttl_seconds=30.0,
        )
        with lock:
            successes.append(got is not None)

    threads = [threading.Thread(target=worker, args=(f"h{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for s in successes if s) == 1
