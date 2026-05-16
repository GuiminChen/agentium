"""Cross-instance session checkpoint continuity (Anthropic managed agents #16)."""

from __future__ import annotations

from pathlib import Path

from agentium.infra.db.sqlite_store import SqliteSessionCheckpointStore


def test_checkpoint_store_reopen_sees_prior_writes(tmp_path: Path) -> None:
    """Simulate process A exit and process B resume against the same SQLite file."""

    db = tmp_path / "shared_checkpoints.db"
    first = SqliteSessionCheckpointStore(db)
    seq = first.append(
        session_id="run-resume-demo",
        tenant_id="tenant-a",
        label="after_turn_1",
        payload={"tokens": 42, "node": "planner"},
    )
    assert seq == 1
    first.close()

    second = SqliteSessionCheckpointStore(db)
    rows = second.list_for_session(session_id="run-resume-demo", tenant_id="tenant-a")
    assert len(rows) == 1
    assert rows[0]["seq"] == 1
    assert rows[0]["payload"]["node"] == "planner"
    got = second.get(session_id="run-resume-demo", tenant_id="tenant-a", seq=1)
    assert got is not None
    assert got["payload"]["tokens"] == 42
    second.close()
