"""Policy guard for the background plane (governed outer-loop actions)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from agentium.governance.policy_engine import PolicyEngine
from agentium.models.context import DecisionType, RequestContext


@dataclass(frozen=True)
class GuardDecision:
    """Decision returned by BackgroundPolicyGuard."""

    allowed: bool
    reason: str
    rule_id: Optional[str]


class BackgroundPolicyGuard:
    """Wraps PolicyEngine to enforce background-side action gating.

    Background triggers MUST flow through this guard; any background action
    that would call a tool, change state, or notify externally is short-circuited
    here when the active policy denies it. This is the hard fence that prevents
    the background plane from bypassing governance.
    """

    def __init__(self, policy_engine: PolicyEngine) -> None:
        self._policy_engine = policy_engine

    def check(
        self,
        context: RequestContext,
        tool_name: str,
        call_args: Optional[Dict[str, Any]] = None,
    ) -> GuardDecision:
        """Evaluate the policy decision for one prospective background action."""

        decision = self._policy_engine.decide_tool_call(context, tool_name, call_args or {})
        return GuardDecision(
            allowed=decision.decision == DecisionType.ALLOW,
            reason=decision.reason,
            rule_id=decision.rule_id,
        )
