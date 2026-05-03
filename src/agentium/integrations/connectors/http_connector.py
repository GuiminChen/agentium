"""HTTP connector implementation for external API integration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional
from urllib import error, parse, request

from agentium.shared.errors import AgentiumError


class ConnectorExecutionError(AgentiumError):
    """Raised when one connector request fails."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class HTTPRequest:
    """One normalized HTTP request model."""

    method: str
    path: str
    query: Dict[str, str] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    json_body: Optional[Dict[str, Any]] = None
    timeout_seconds: float = 5.0


@dataclass(frozen=True)
class HTTPResponse:
    """One normalized HTTP response model."""

    status_code: int
    headers: Dict[str, str]
    body: bytes


Transport = Callable[[HTTPRequest], HTTPResponse]


class HTTPConnector:
    """Connector for standard HTTP-based upstream systems."""

    def __init__(
        self,
        base_url: str,
        default_headers: Optional[Dict[str, str]] = None,
        transport: Optional[Transport] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._default_headers = default_headers or {}
        self._transport = transport or self._default_transport

    def execute(self, payload: HTTPRequest) -> Dict[str, Any]:
        """Execute one HTTP call and return normalized response payload."""

        response = self._transport(payload)
        if response.status_code < 200 or response.status_code >= 300:
            raise ConnectorExecutionError(
                message="HTTP connector upstream rejected request",
                status_code=response.status_code,
            )
        data = self._parse_body(response.body)
        return {"status_code": response.status_code, "headers": response.headers, "data": data}

    def _default_transport(self, payload: HTTPRequest) -> HTTPResponse:
        url = self._build_url(path=payload.path, query=payload.query)
        encoded_body: Optional[bytes] = None
        headers = dict(self._default_headers)
        headers.update(payload.headers)
        if payload.json_body is not None:
            encoded_body = json.dumps(payload.json_body, ensure_ascii=False).encode("utf-8")
            if "Content-Type" not in headers:
                headers["Content-Type"] = "application/json"
        req = request.Request(url=url, data=encoded_body, method=payload.method.upper(), headers=headers)
        try:
            with request.urlopen(req, timeout=payload.timeout_seconds) as resp:
                return HTTPResponse(
                    status_code=resp.getcode(),
                    headers=dict(resp.headers.items()),
                    body=resp.read(),
                )
        except error.HTTPError as exc:
            return HTTPResponse(
                status_code=exc.code,
                headers=dict(exc.headers.items()) if exc.headers else {},
                body=exc.read(),
            )
        except error.URLError as exc:
            raise ConnectorExecutionError(message="HTTP connector network failure: " + str(exc))

    def _build_url(self, path: str, query: Dict[str, str]) -> str:
        normalized_path = path if path.startswith("/") else "/" + path
        url = self._base_url + normalized_path
        if not query:
            return url
        return url + "?" + parse.urlencode(query)

    @staticmethod
    def _parse_body(body: bytes) -> Any:
        if not body:
            return {}
        text = body.decode("utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw_text": text}
