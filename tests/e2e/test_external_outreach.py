"""E2E: external write tool is denied by default-deny policy."""

from __future__ import annotations


def test_external_write_blocked_by_default(http_server) -> None:
    client, _ = http_server
    status, body = client.post(
        "/v1/turn",
        body={
            "tool_name": "risky",
            "args": {"channel": "email"},
            "run_id": "run-out-1",
            "request_id": "req-1",
            "trace_id": "trace-1",
        },
        headers={"X-Tenant-Id": "tenant-out", "X-User-Id": "user-1"},
    )
    assert status == 200
    assert body["status"] == "blocked"
