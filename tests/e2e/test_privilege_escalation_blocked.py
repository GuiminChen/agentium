"""E2E: empty tenant header is rejected at the API gate."""

from __future__ import annotations


def test_missing_tenant_blocked(http_server) -> None:
    client, _ = http_server
    status, body = client.post(
        "/v1/turn",
        body={
            "tool_name": "echo",
            "args": {},
            "run_id": "run-esc-1",
            "request_id": "req-1",
            "trace_id": "trace-1",
        },
        headers={"X-User-Id": "user-1"},
    )
    assert status == 401
    assert body["error"] == "missing_identity_headers"


def test_invalid_tenant_chars_rejected(http_server) -> None:
    client, _ = http_server
    status, body = client.post(
        "/v1/turn",
        body={
            "tool_name": "echo",
            "args": {},
            "run_id": "run-esc-2",
            "request_id": "req-1",
            "trace_id": "trace-1",
        },
        headers={"X-Tenant-Id": "bad/tenant", "X-User-Id": "user-1"},
    )
    assert status == 400
    assert body["error"] == "tenant_id_invalid_chars"
