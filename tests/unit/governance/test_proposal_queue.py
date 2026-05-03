"""Unit tests for :class:`ProposalQueue` covering lifecycle and audit events."""

from __future__ import annotations

from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.proposal_queue import (
    ProposalKind,
    ProposalQueue,
    ProposalStatus,
)
from agentium.models.context import RequestContext


def _context(tenant: str = "tenant-a", user: str = "user-1") -> RequestContext:
    return RequestContext(
        request_id="req-1",
        run_id="run-1",
        tenant_id=tenant,
        user_id=user,
        trace_id="trace-1",
        role="analyst",
    )


def test_submit_creates_pending_proposal_with_audit() -> None:
    sink = InMemoryAuditSink()
    queue = ProposalQueue(audit_sink=sink)

    proposal = queue.submit(
        context=_context(),
        kind=ProposalKind.MEMORY_PROMOTION,
        payload={"segment_id": "seg-42", "summary": "promote"},
    )

    assert proposal.status == ProposalStatus.PENDING
    assert proposal.tenant_id == "tenant-a"
    assert proposal.kind == ProposalKind.MEMORY_PROMOTION

    events = [e for e in sink.query() if e.event_type == "proposal_state_changed"]
    assert len(events) == 1
    assert events[0].payload["transition"] == "submitted"
    assert events[0].payload["status"] == "pending"
    assert events[0].payload["proposal_id"] == proposal.proposal_id


def test_approve_marks_approved_and_emits_decision_audit() -> None:
    sink = InMemoryAuditSink()
    queue = ProposalQueue(audit_sink=sink)
    submitted = queue.submit(
        context=_context(),
        kind=ProposalKind.POLICY_DELTA,
        payload={"diff": "+rule/A"},
        policy_version="pol-v3",
    )

    decided = queue.approve(submitted.proposal_id, reviewer_id="reviewer-1", comment="ok")

    assert decided is not None
    assert decided.status == ProposalStatus.APPROVED
    assert decided.reviewer_id == "reviewer-1"
    assert decided.decided_at is not None

    transitions = [
        e.payload["transition"]
        for e in sink.query()
        if e.event_type == "proposal_state_changed"
    ]
    assert transitions == ["submitted", "approved"]
    approved_event = [
        e for e in sink.query() if e.payload.get("transition") == "approved"
    ][0]
    assert approved_event.policy_version == "pol-v3"


def test_reject_terminal_state_prevents_further_transitions() -> None:
    queue = ProposalQueue(audit_sink=InMemoryAuditSink())
    submitted = queue.submit(
        context=_context(),
        kind=ProposalKind.PREFERENCE_UPDATE,
        payload={"pref": "concise"},
    )

    rejected = queue.reject(submitted.proposal_id, reviewer_id="reviewer-2", comment="no")
    assert rejected is not None
    assert rejected.status == ProposalStatus.REJECTED

    second_attempt = queue.approve(submitted.proposal_id, reviewer_id="reviewer-3")
    assert second_attempt is None


def test_list_pending_filters_by_tenant_and_kind() -> None:
    queue = ProposalQueue()
    queue.submit(
        context=_context(tenant="tenant-a"),
        kind=ProposalKind.MEMORY_PROMOTION,
        payload={"k": 1},
    )
    queue.submit(
        context=_context(tenant="tenant-b"),
        kind=ProposalKind.PROMPT_TEMPLATE,
        payload={"k": 2},
    )

    tenant_a = queue.list_pending(tenant_id="tenant-a")
    tenant_a_memory = queue.list_pending(tenant_id="tenant-a", kind=ProposalKind.MEMORY_PROMOTION)
    tenant_b_memory = queue.list_pending(tenant_id="tenant-b", kind=ProposalKind.MEMORY_PROMOTION)

    assert len(tenant_a) == 1
    assert tenant_a[0].tenant_id == "tenant-a"
    assert len(tenant_a_memory) == 1
    assert tenant_b_memory == []


def test_withdraw_by_submitter_records_transition() -> None:
    sink = InMemoryAuditSink()
    queue = ProposalQueue(audit_sink=sink)
    submitted = queue.submit(
        context=_context(),
        kind=ProposalKind.PROMPT_TEMPLATE,
        payload={"template": "..."},
    )

    withdrawn = queue.withdraw(submitted.proposal_id, actor_id="user-1", comment="retry later")

    assert withdrawn is not None
    assert withdrawn.status == ProposalStatus.WITHDRAWN
    transitions = [
        e.payload["transition"]
        for e in sink.query()
        if e.event_type == "proposal_state_changed"
    ]
    assert transitions[-1] == "withdrawn"


def test_get_returns_deep_copy_preventing_external_mutation() -> None:
    queue = ProposalQueue()
    proposal = queue.submit(
        context=_context(),
        kind=ProposalKind.MEMORY_PROMOTION,
        payload={"key": "value"},
    )

    snapshot = queue.get(proposal.proposal_id)
    assert snapshot is not None
    snapshot.payload["key"] = "mutated"

    fresh = queue.get(proposal.proposal_id)
    assert fresh is not None
    assert fresh.payload == {"key": "value"}
