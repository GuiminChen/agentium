"""Resolved identity tuple for HTTP control plane."""

from __future__ import annotations

from typing import List, NamedTuple, Tuple


class IdentityInfo(NamedTuple):
    """Resolved caller identity for control-plane requests."""

    tenant_id: str
    user_id: str
    role: str
    roles: Tuple[str, ...]


def make_identity_info(tenant_id: str, user_id: str, roles: List[str]) -> IdentityInfo:
    ordered = sorted(set(roles)) if roles else ["user"]
    primary = ordered[0]
    return IdentityInfo(tenant_id, user_id, primary, tuple(ordered))


def is_platform_ops_role(role: str, roles: Tuple[str, ...]) -> bool:
    return "platform_ops" in roles or role == "platform_ops"
