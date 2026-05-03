"""Background plane: governed daemon, triggers, and outer-loop hooks."""

from agentium.background.background_daemon import (
    BackgroundDaemon,
    BackgroundTriggerHandler,
    FullTickReport,
)
from agentium.background.event_ingestor import EventIngestor, IngestedEvent
from agentium.background.memory_consolidator import (
    ConsolidationReport,
    MemoryConsolidator,
)
from agentium.background.notify_bridge import NotifyBridge, NotifyRequest
from agentium.background.policy_guard import BackgroundPolicyGuard, GuardDecision
from agentium.background.trigger_planner import (
    PlannerResult,
    ProposedAction,
    TriggerPlanner,
    TriggerRule,
)
from agentium.background.triggers import (
    CallbackTrigger,
    IntervalTrigger,
    TriggerEvent,
    utc_now,
)

__all__ = [
    "BackgroundDaemon",
    "BackgroundPolicyGuard",
    "BackgroundTriggerHandler",
    "CallbackTrigger",
    "ConsolidationReport",
    "EventIngestor",
    "FullTickReport",
    "GuardDecision",
    "IngestedEvent",
    "IntervalTrigger",
    "MemoryConsolidator",
    "NotifyBridge",
    "NotifyRequest",
    "PlannerResult",
    "ProposedAction",
    "TriggerEvent",
    "TriggerPlanner",
    "TriggerRule",
    "utc_now",
]
