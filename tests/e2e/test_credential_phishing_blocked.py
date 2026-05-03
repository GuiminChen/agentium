"""E2E: DLP blocks tool output containing private key material."""

from __future__ import annotations


def test_dlp_blocks_private_key(http_server) -> None:
    client, container = http_server
    container.tool_registry._policy_engine = container.tool_registry._policy_engine  # noqa: SLF001
    status, body = client.post(
        "/v1/turn",
        body={
            "tool_name": "leak",
            "args": {},
            "run_id": "run-cred-1",
            "request_id": "req-1",
            "trace_id": "trace-1",
        },
        headers={"X-Tenant-Id": "tenant-cred", "X-User-Id": "user-1"},
    )
    assert status == 200
    assert body["status"] == "blocked"
