"""Uniform JSON error bodies: ``error`` (code), optional ``message``, optional ``detail``."""

from __future__ import annotations

from typing import Any, Dict


def error_payload(error: str, message: str = "", detail: Any = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"error": error}
    if message:
        payload["message"] = message
    if detail is not None:
        payload["detail"] = detail
    return payload
