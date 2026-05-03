"""E2E: health and ready endpoints respond after bootstrap."""

from __future__ import annotations


def test_healthz_and_readyz(http_server) -> None:
    client, _ = http_server
    status, body = client.get("/v1/healthz")
    assert status == 200
    assert body["status"] == "ok"
    status, body = client.get("/v1/readyz")
    assert status == 200
    assert body["status"] == "ready"
