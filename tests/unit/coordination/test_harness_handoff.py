"""Tests for harness handoff key verification."""

from __future__ import annotations

from agentium.coordination.artifact_store import ArtifactStore
from agentium.coordination.harness_handoff import verify_handoff_artifact_keys


def test_verify_handoff_empty_keys_ok() -> None:
    store = ArtifactStore()
    r = verify_handoff_artifact_keys(store, tenant_id="t", run_id="r", keys=[])
    assert r.ok is True


def test_verify_handoff_matches_content_field() -> None:
    store = ArtifactStore()
    store.put(
        workflow="wf",
        node="n",
        tenant_id="t",
        run_id="r",
        content={"summary.json": {"v": 1}},
        parent_ids=(),
    )
    r = verify_handoff_artifact_keys(store, tenant_id="t", run_id="r", keys=["summary.json"])
    assert r.ok is True


def test_verify_handoff_matches_metadata_handoff_key() -> None:
    store = ArtifactStore()
    store.put(
        workflow="wf",
        node="n",
        tenant_id="t",
        run_id="r",
        content={"x": 1},
        parent_ids=(),
        metadata={"handoff_key": "artifact_a"},
    )
    r = verify_handoff_artifact_keys(store, tenant_id="t", run_id="r", keys=["artifact_a"])
    assert r.ok is True


def test_verify_handoff_missing() -> None:
    store = ArtifactStore()
    store.put(
        workflow="wf",
        node="n",
        tenant_id="t",
        run_id="r",
        content={"other": True},
        parent_ids=(),
    )
    r = verify_handoff_artifact_keys(store, tenant_id="t", run_id="r", keys=["need_this"])
    assert r.ok is False
    assert "need_this" in r.missing_keys
