"""Legacy HTTP adapter with field mapping and retry."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Set

from agentium.integrations.connectors.http_connector import (
    ConnectorExecutionError,
    HTTPConnector,
    HTTPRequest,
)
from agentium.shared.errors import ConfigurationError


class LegacyHTTPConnector:
    """Legacy HTTP wrapper for schema translation and bounded retries."""

    def __init__(
        self,
        http_connector: HTTPConnector,
        method: str,
        path: str,
        request_field_mapping: Mapping[str, str],
        response_field_mapping: Mapping[str, str],
        max_retries: int = 0,
        retry_status_codes: Set[int] = frozenset({502, 503, 504}),
    ) -> None:
        self._http_connector = http_connector
        self._method = method
        self._path = path
        self._request_field_mapping = dict(request_field_mapping)
        self._response_field_mapping = dict(response_field_mapping)
        self._max_retries = max_retries
        self._retry_status_codes = set(retry_status_codes)

    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Execute one legacy request with field translation."""

        params = self._extract_params(request=request)
        mapped_body = self._map_fields(params, self._request_field_mapping)
        attempts = 0
        while True:
            attempts += 1
            try:
                result = self._http_connector.execute(
                    HTTPRequest(method=self._method, path=self._path, json_body=mapped_body)
                )
                data = result.get("data", {})
                if isinstance(data, dict):
                    return self._map_fields(data, self._response_field_mapping)
                return {"result": data}
            except ConnectorExecutionError as exc:
                if attempts > self._max_retries + 1:
                    raise
                if exc.status_code not in self._retry_status_codes:
                    raise

    @staticmethod
    def _extract_params(request: Dict[str, Any]) -> Dict[str, Any]:
        params = request.get("params", {})
        if not isinstance(params, dict):
            raise ConfigurationError("Legacy connector expects params mapping")
        return params

    @staticmethod
    def _map_fields(source: Mapping[str, Any], field_mapping: Mapping[str, str]) -> Dict[str, Any]:
        if not field_mapping:
            return dict(source)
        mapped: Dict[str, Any] = {}
        for source_key, target_key in field_mapping.items():
            if source_key in source:
                mapped[target_key] = source[source_key]
        return mapped
