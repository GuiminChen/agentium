from __future__ import annotations

from agentium.api.runtime_response import RuntimeTurnResponse, map_runtime_result_to_response
from agentium.core.agent_runtime import RuntimeResult, RuntimeStatus


def test_map_completed_runtime_result_to_api_response() -> None:
    result = RuntimeResult(
        status=RuntimeStatus.COMPLETED,
        tool_name="echo",
        output={"message": "ok"},
        tool_use_id="tool-1",
    )

    response = map_runtime_result_to_response(result)

    assert isinstance(response, RuntimeTurnResponse)
    assert response.status == "completed"
    assert response.error_code is None
    assert response.approval_id is None
    assert response.output == {"message": "ok"}
    assert response.references == []


def test_map_pending_approval_runtime_result_to_api_response() -> None:
    result = RuntimeResult(
        status=RuntimeStatus.PENDING_APPROVAL,
        tool_name="db_export",
        approval_id="approval-1",
        message="export requires approval",
        error_code="approval_required",
    )

    response = map_runtime_result_to_response(result)

    assert response.status == "pending_approval"
    assert response.error_code == "approval_required"
    assert response.approval_id == "approval-1"
    assert response.message == "export requires approval"


def test_map_blocked_runtime_result_to_api_response() -> None:
    result = RuntimeResult(
        status=RuntimeStatus.BLOCKED,
        tool_name="echo",
        message="Prompt injection risk blocked",
        error_code="policy_denied",
    )

    response = map_runtime_result_to_response(result)

    assert response.status == "blocked"
    assert response.error_code == "policy_denied"
    assert response.message == "Prompt injection risk blocked"
    assert response.output is None
