"""E2E: simple turn over /v1/turn returns success for read-only tool."""

from __future__ import annotations


def test_turn_returns_completed(http_server) -> None:
    client, _ = http_server
    status, body = client.post(
        "/v1/turn",
        body={
            "tool_name": "echo",
            "args": {"q": "hello"},
            "run_id": "run-deep-1",
            "request_id": "req-1",
            "trace_id": "trace-1",
            "deployment_mode": "dev",
        },
        headers={"X-Tenant-Id": "tenant-research", "X-User-Id": "user-1"},
    )
    assert status == 200
    assert body["status"] == "completed"
