"""Server-side capability tokens for GET /v1/me (PRD §1.3)."""

from __future__ import annotations

import os
from typing import Dict, FrozenSet, List


_BASE_CAPABILITIES: FrozenSet[str] = frozenset({"health.read", "me.read"})

# Role → capability sets (union for multi-role principals).
_ROLE_CAPABILITIES: Dict[str, FrozenSet[str]] = {
    "user": frozenset(
        {
            "turn.execute",
            "tools.read",
            "approval.read",
            "audit.read",
            "runs.read",
            "sessions.read",
            "sessions.checkpoint",
            "export.audit.redacted",
            "eval.read",
            "research.run",
            "workflow.read",
            "artifacts.read",
            "connectors.read",
            "runs.cancel",
            "chat.sessions.manage",
            "chat.messages.read",
            "chat.messages.send",
        }
    ),
    "admin": frozenset(
        {
            "turn.execute",
            "tools.read",
            "approval.read",
            "approval.decide",
            "audit.read",
            "runs.read",
            "sessions.read",
            "sessions.checkpoint",
            "governance.policy.read",
            "governance.releases.read",
            "observability.read",
            "policy.release.submit",
            "budget.read",
            "background.read",
            "background.control",
            "export.audit.redacted",
            "eval.run",
            "eval.read",
            "research.run",
            "workflow.read",
            "workflow.intervene",
            "artifacts.read",
            "connectors.read",
            "security.events.read",
            "evolution.submit",
            "governance.packs.read",
            "eval.compare",
            "runs.cancel",
            "chat.sessions.manage",
            "chat.messages.read",
            "chat.messages.send",
        }
    ),
    "tenant_admin": frozenset(
        {
            "turn.execute",
            "tools.read",
            "approval.read",
            "approval.decide",
            "audit.read",
            "runs.read",
            "sessions.read",
            "sessions.checkpoint",
            "governance.policy.read",
            "governance.releases.read",
            "observability.read",
            "policy.release.submit",
            "budget.read",
            "background.read",
            "background.control",
            "export.audit.redacted",
            "eval.run",
            "eval.read",
            "research.run",
            "workflow.read",
            "workflow.intervene",
            "artifacts.read",
            "connectors.read",
            "security.events.read",
            "evolution.submit",
            "governance.packs.read",
            "eval.compare",
            "runs.cancel",
            "chat.sessions.manage",
            "chat.messages.read",
            "chat.messages.send",
        }
    ),
    "platform_ops": frozenset(
        {
            "turn.execute",
            "tools.read",
            "approval.read",
            "approval.decide",
            "audit.read",
            "runs.read",
            "sessions.read",
            "sessions.checkpoint",
            "governance.policy.read",
            "governance.releases.read",
            "observability.read",
            "policy.release.submit",
            "platform.ops",
            "platform.breakglass.read",
            "budget.read",
            "background.read",
            "background.control",
            "export.audit.redacted",
            "eval.run",
            "eval.read",
            "research.run",
            "workflow.read",
            "workflow.intervene",
            "artifacts.read",
            "connectors.read",
            "security.events.read",
            "evolution.submit",
            "governance.packs.read",
            "eval.compare",
            "runs.cancel",
            "chat.sessions.manage",
            "chat.messages.read",
            "chat.messages.send",
        }
    ),
    "guest": frozenset(),
}


def deployment_mode_from_env() -> str:
    return os.environ.get("AGENTIUM_DEPLOYMENT_MODE", "prod").strip() or "prod"


def capabilities_for_roles(roles: List[str]) -> List[str]:
    caps: set[str] = set(_BASE_CAPABILITIES)
    for role in roles:
        caps |= _ROLE_CAPABILITIES.get(role, _ROLE_CAPABILITIES["user"])
    return sorted(caps)


def ui_profile_for_roles(roles: List[str]) -> str:
    if "platform_ops" in roles or "admin" in roles or "tenant_admin" in roles:
        return "enterprise"
    return "minimal"
