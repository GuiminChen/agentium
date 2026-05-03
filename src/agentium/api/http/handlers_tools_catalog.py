"""Read-only tool catalog HTTP handler."""

from __future__ import annotations

from http import HTTPStatus

from agentium.api.http.handler_constants import cap_granted


class ToolCatalogHandlersMixin:
    """Mixed into ``ControlPlaneHTTPRequestHandler``."""

    def _handle_tools_catalog(self) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "tools.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability tools.read required.")
            return
        payload = self.api.list_tool_catalog()
        self._write_json(HTTPStatus.OK, payload)
