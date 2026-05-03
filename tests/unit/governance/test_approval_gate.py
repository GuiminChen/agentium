from __future__ import annotations

from agentium.governance.approval_gate import ApprovalGate, ApprovalStatus
from agentium.models.context import RequestContext


def _context() -> RequestContext:
    return RequestContext(
        request_id="req-1",
        run_id="run-1",
        tenant_id="tenant-a",
        user_id="user-1",
        trace_id="trace-1",
        role="admin",
        deployment_mode="prod",
    )


def test_approval_gate_request_and_approve() -> None:
    gate = ApprovalGate()
    request = gate.request_approval(
        context=_context(),
        tool_name="db_export",
        reason="high risk",
        args_hash="abc",
    )

    approved = gate.approve(request.approval_id, approver_id="reviewer-1", comment="ok")
    refreshed = gate.get_request(request.approval_id)

    assert approved is True
    assert refreshed is not None
    assert refreshed.status == ApprovalStatus.APPROVED
    assert refreshed.approver_id == "reviewer-1"


def test_approval_gate_reject() -> None:
    gate = ApprovalGate()
    request = gate.request_approval(
        context=_context(),
        tool_name="db_export",
        reason="high risk",
        args_hash="abc",
    )

    rejected = gate.reject(request.approval_id, approver_id="reviewer-2", comment="no")
    refreshed = gate.get_request(request.approval_id)

    assert rejected is True
    assert refreshed is not None
    assert refreshed.status == ApprovalStatus.REJECTED
