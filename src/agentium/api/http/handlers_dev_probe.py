"""Development-only HTTP probes (no production surface unless explicitly enabled)."""

from __future__ import annotations

from http import HTTPStatus


class DevProbeHandlersMixin:
    """Mixed into :class:`ControlPlaneHTTPRequestHandler`."""

    def _handle_dev_lsp_capabilities(self) -> None:
        """Return LSP bridge RFC pointer and upstream configuration flag (dev only)."""

        res = self.resources
        if res is None or not res.dev_http_enabled:
            self._write_error(HTTPStatus.NOT_FOUND, "endpoint_not_found", "Unknown GET path.")
            return
        self._write_json(
            HTTPStatus.OK,
            {
                "rfc_path": "docs/architecture/rfc-session-lsp-bridge.md",
                "lsp_upstream_configured": bool(res.lsp_upstream_configured),
                "websocket_proxy_available": False,
                "notes": "MVP: no WebSocket LSP proxy; use RFC for boundaries and future milestones.",
            },
        )


__all__ = ["DevProbeHandlersMixin"]
