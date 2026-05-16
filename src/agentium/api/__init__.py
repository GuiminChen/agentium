"""API interface package."""

from __future__ import annotations

from typing import Any

from agentium.api.control_plane import (
    ApprovalDecisionResponse,
    ApprovalDecisionType,
    ApprovalStateResponse,
    ControlPlaneAPI,
)
from agentium.api.runtime_response import RuntimeTurnResponse, map_runtime_result_to_response

__all__ = [
    "ApprovalDecisionResponse",
    "ApprovalDecisionType",
    "ApprovalStateResponse",
    "ControlPlaneAPI",
    "RuntimeTurnResponse",
    "build_http_server",
    "map_runtime_result_to_response",
]


def __getattr__(name: str) -> Any:
    """Lazy-import HTTP server builder so coordination can import ``ControlPlaneAPI`` without cycles."""
    if name == "build_http_server":
        from agentium.api.http_control_plane import build_http_server as _build_http_server

        return _build_http_server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
