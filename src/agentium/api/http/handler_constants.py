"""Shared HTTP control-plane constants and role helpers."""

from __future__ import annotations

from typing import Any, Tuple

from agentium.api.http.capabilities import capabilities_for_roles

TENANT_BAD_CHARS = set("\r\n\t\0/\\")

# Sentinel for manifest validation failure (response already written).
MANIFEST_REJECTED: Any = object()


def cap_granted(roles: Tuple[str, ...], cap: str) -> bool:
    return cap in capabilities_for_roles(list(roles))


def admin_scope(roles: Tuple[str, ...]) -> bool:
    return bool(set(roles) & {"admin", "tenant_admin", "platform_ops"})
