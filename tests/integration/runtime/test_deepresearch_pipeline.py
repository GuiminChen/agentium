"""Integration tests for :class:`DeepResearchPipeline`."""

from __future__ import annotations

from agentium.coordination.artifact_store import ArtifactStore
from agentium.coordination.workflow_orchestrator import NodeStatus
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.models.context import RequestContext
from agentium.runtime.deepresearch_pipeline import (
    DeepResearchPipeline,
    ResearchHandlers,
    stub_handlers,
)


def _ctx(run_id: str = "run-research-1") -> RequestContext:
    return RequestContext(
        request_id="req-1",
        run_id=run_id,
        tenant_id="tenant-research",
        user_id="user-1",
        trace_id="trace-1",
    )


def test_pipeline_runs_all_stages_with_stub_handlers() -> None:
    artifacts = ArtifactStore()
    audit = InMemoryAuditSink()
    pipeline = DeepResearchPipeline(
        handlers=stub_handlers(),
        artifact_store=artifacts,
        audit_sink=audit,
    )
    outcome = pipeline.run(context=_ctx(), query="agent governance")

    assert outcome.success is True
    assert outcome.report is not None
    assert outcome.report["title"].startswith("Research report")
    assert len(outcome.artifacts) == 5
    statuses = {n: r.status for n, r in outcome.state.completed_nodes.items()}
    for stage in ("plan", "search", "synthesize", "critique", "report"):
        assert statuses[stage] == NodeStatus.COMPLETED


def test_pipeline_vertical_template_echoed_in_stub_report() -> None:
    pipeline = DeepResearchPipeline(handlers=stub_handlers(), artifact_store=ArtifactStore())
    outcome = pipeline.run(
        context=_ctx(run_id="run-vert"),
        query="risk",
        extras={"vertical_template": "fixed_income"},
    )
    assert outcome.success is True
    assert outcome.report is not None
    assert outcome.report.get("vertical_pack_semver")
    assert outcome.report.get("vertical_template") == "fixed_income"
    assert "[fixed_income]" in outcome.report["title"]


def test_pipeline_records_artifact_lineage_for_audit() -> None:
    artifacts = ArtifactStore()
    pipeline = DeepResearchPipeline(handlers=stub_handlers(), artifact_store=artifacts)
    pipeline.run(context=_ctx(run_id="run-lineage"), query="multi-tenant safety")

    stored = artifacts.list_for_run(run_id="run-lineage")
    nodes = sorted(a.node for a in stored)
    assert nodes == ["critique", "plan", "report", "search", "synthesize"]
    for artifact in stored:
        assert artifact.tenant_id == "tenant-research"
        assert artifact.workflow == "deepresearch.v1"


def test_pipeline_marks_failure_when_required_stage_returns_invalid_artifact() -> None:
    handlers = stub_handlers()

    def bad_report(_, __):
        return {"title": "missing fields"}

    custom = ResearchHandlers(
        plan=handlers.plan,
        search=handlers.search,
        synthesize=handlers.synthesize,
        critique=handlers.critique,
        report=bad_report,
    )
    pipeline = DeepResearchPipeline(handlers=custom, artifact_store=ArtifactStore())
    outcome = pipeline.run(context=_ctx(run_id="run-bad"), query="incomplete report")

    assert outcome.success is False
    report_node = outcome.state.completed_nodes["report"]
    assert report_node.status == NodeStatus.FAILED
    assert report_node.artifact_validation is not None
    assert "missing_required_keys" in (report_node.artifact_validation.reason or "")


def test_pipeline_invokes_evolution_plugin_after_run() -> None:
    class Capture:
        def __init__(self) -> None:
            self.batches: list = []

        def on_trajectory(self, context, batch) -> None:  # type: ignore[no-untyped-def]
            self.batches.append(batch)

    cap = Capture()
    pipeline = DeepResearchPipeline(
        handlers=stub_handlers(),
        artifact_store=ArtifactStore(),
        evolution_plugin=cap,
    )
    pipeline.run(context=_ctx(run_id="run-evo"), query="governance")

    assert len(cap.batches) == 1
    batch = cap.batches[0]
    assert batch.run_id == "run-evo"
    names = {e.step_type for e in batch.events}
    assert "deep_research.run.summary" in names
    assert "deep_research.node.plan" in names
