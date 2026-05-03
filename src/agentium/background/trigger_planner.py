"""TriggerPlanner: convert ingested events into proposed actions.

Per technical design (background plane), the planner inspects buffered events and
emits two queues:

- **suggestions** – low-risk notifications dispatched directly via
  :class:`NotifyBridge`;
- **approval_required** – higher-risk actions handed to the approval gate
  before any side effects.

The planner is rule-based and deterministic so that the auditor can map
every emitted action back to one input event without consulting an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Mapping, Optional, Sequence

from agentium.background.event_ingestor import IngestedEvent


@dataclass(frozen=True)
class TriggerRule:
    """Declarative mapping from event topic to proposed action."""

    topic: str
    action: str
    risk: str = "low"
    description: str = ""
    payload_keys: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class ProposedAction:
    """One concrete action emitted by :class:`TriggerPlanner`."""

    action: str
    risk: str
    tenant_id: str
    summary: str
    payload: Mapping[str, object]
    source_event: IngestedEvent
    requires_approval: bool


@dataclass
class PlannerResult:
    """Aggregate output of :meth:`TriggerPlanner.plan`."""

    suggestions: List[ProposedAction] = field(default_factory=list)
    approval_required: List[ProposedAction] = field(default_factory=list)


class TriggerPlanner:
    """Plan background-plane actions based on ingested events and declarative rules.

    Args:
        rules: ``topic -> TriggerRule`` mapping.  A topic without a rule is
            ignored (the operator can later add one).
        high_risk_levels: risks that require human approval before action.
    """

    def __init__(
        self,
        rules: Iterable[TriggerRule],
        *,
        high_risk_levels: Sequence[str] = ("high", "critical"),
    ) -> None:
        self._rules = {rule.topic: rule for rule in rules}
        self._high_risk = set(high_risk_levels)

    def plan(self, events: Sequence[IngestedEvent]) -> PlannerResult:
        result = PlannerResult()
        for event in events:
            rule = self._rules.get(event.topic)
            if rule is None:
                continue
            tenant_id = str(event.payload.get("tenant_id") or event.headers.get("tenant_id") or "")
            if not tenant_id:
                continue
            summary = rule.description or f"{rule.action} from {event.topic}"
            payload_keys = rule.payload_keys or list(event.payload.keys())
            payload = {
                key: event.payload.get(key)
                for key in payload_keys
                if key in event.payload
            }
            requires_approval = rule.risk in self._high_risk
            action = ProposedAction(
                action=rule.action,
                risk=rule.risk,
                tenant_id=tenant_id,
                summary=summary,
                payload=payload,
                source_event=event,
                requires_approval=requires_approval,
            )
            if requires_approval:
                result.approval_required.append(action)
            else:
                result.suggestions.append(action)
        return result


__all__ = ["PlannerResult", "ProposedAction", "TriggerPlanner", "TriggerRule"]
