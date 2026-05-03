from __future__ import annotations

import pytest

from agentium.integrations.connectors.http_connector import (
    ConnectorExecutionError,
    HTTPConnector,
    HTTPRequest,
    HTTPResponse,
)


def test_http_connector_returns_json_payload() -> None:
    connector = HTTPConnector(
        base_url="https://api.example.com",
        transport=lambda request: HTTPResponse(
            status_code=200,
            headers={"Content-Type": "application/json"},
            body=b'{"status":"ok","order_id":"ord-1"}',
        ),
    )

    result = connector.execute(
        HTTPRequest(
            method="POST",
            path="/v1/orders",
            json_body={"amount": 100},
            headers={"X-Tenant": "tenant-a"},
            query={"mode": "sync"},
            timeout_seconds=2.0,
        )
    )

    assert result["status_code"] == 200
    assert result["data"]["status"] == "ok"
    assert result["data"]["order_id"] == "ord-1"


def test_http_connector_raises_on_non_success() -> None:
    connector = HTTPConnector(
        base_url="https://api.example.com",
        transport=lambda request: HTTPResponse(
            status_code=503,
            headers={"Content-Type": "application/json"},
            body=b'{"error":"upstream_down"}',
        ),
    )

    with pytest.raises(ConnectorExecutionError):
        connector.execute(HTTPRequest(method="GET", path="/v1/orders/ord-1"))
