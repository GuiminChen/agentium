"""Unit tests for the content-addressable ArtifactStore."""

from __future__ import annotations

from pathlib import Path

from agentium.coordination.artifact_store import (
    Artifact,
    ArtifactStore,
    compute_artifact_id,
    make_idempotency_key,
)


def test_artifact_store_assigns_deterministic_ids(tmp_path: Path):
    store = ArtifactStore()
    a = store.put(
        workflow="wf", node="n1", tenant_id="t", run_id="r1", content={"x": 1}
    )
    b = store.put(
        workflow="wf", node="n1", tenant_id="t", run_id="r1", content={"x": 1}
    )
    assert a.artifact_id == b.artifact_id
    assert a.artifact_id == compute_artifact_id({"x": 1})


def test_artifact_store_persists_and_replays(tmp_path: Path):
    persist = tmp_path / "art.jsonl"
    store = ArtifactStore(persist_path=persist)
    store.put(workflow="wf", node="n1", tenant_id="t", run_id="r1", content={"v": 1})
    store.put(workflow="wf", node="n2", tenant_id="t", run_id="r1", content={"v": 2})

    fresh = ArtifactStore(persist_path=persist)
    nodes = sorted(a.node for a in fresh.replay())
    assert nodes == ["n1", "n2"]


def test_artifact_store_indexes_run_and_node():
    store = ArtifactStore()
    store.put(workflow="wf", node="n1", tenant_id="t", run_id="r1", content={"v": 1})
    store.put(workflow="wf", node="n1", tenant_id="t", run_id="r2", content={"v": 2})
    assert {a.run_id for a in store.list_for_run("r1")} == {"r1"}
    assert {a.content["v"] for a in store.list_for_node("wf", "n1")} == {1, 2}


def test_artifact_store_carries_parent_lineage():
    store = ArtifactStore()
    parent = store.put(
        workflow="wf", node="root", tenant_id="t", run_id="r1", content={"v": 0}
    )
    child = store.put(
        workflow="wf",
        node="leaf",
        tenant_id="t",
        run_id="r1",
        content={"v": 1},
        parent_ids=(parent.artifact_id,),
    )
    assert child.parent_ids == (parent.artifact_id,)


def test_make_idempotency_key_is_stable():
    a = make_idempotency_key("wf", "node", {"x": 1})
    b = make_idempotency_key("wf", "node", {"x": 1})
    assert a == b
