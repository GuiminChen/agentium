from __future__ import annotations

from typing import List

from agentium.integrations.connectors.http_connector import HTTPConnector, HTTPRequest, HTTPResponse
from agentium.integrations.connectors.legacy_http_connector import LegacyHTTPConnector


def test_legacy_http_connector_maps_fields_and_retries_once() -> None:
    calls: List[HTTPRequest] = []

    def _transport(request: HTTPRequest) -> HTTPResponse:
        calls.append(request)
        if len(calls) == 1:
            return HTTPResponse(status_code=502, headers={}, body=b'{"error":"bad_gateway"}')
        return HTTPResponse(
            status_code=200,
            headers={"Content-Type": "application/json"},
            body=b'{"result":"ok","order_id":"ord-9"}',
        )

    base_connector = HTTPConnector(base_url="https://legacy.example.com", transport=_transport)
    legacy_connector = LegacyHTTPConnector(
        http_connector=base_connector,
        method="POST",
        path="/legacy/export",
        request_field_mapping={"customerId": "customer_id", "amount": "amount"},
        response_field_mapping={"order_id": "orderId"},
        max_retries=1,
    )

    output = legacy_connector.execute(
        {"operation": "export", "params": {"customerId": "c-1", "amount": 100}}
    )

    assert len(calls) == 2
    assert calls[0].json_body == {"customer_id": "c-1", "amount": 100}
    assert output["orderId"] == "ord-9"
