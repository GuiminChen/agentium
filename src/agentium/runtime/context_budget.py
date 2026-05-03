"""Context window budgeting with soft compaction and hard stop semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional


class ContextHardStopError(RuntimeError):
    """Raised when context size exceeds the hard stop limit."""

    def __init__(self, limit: int, observed: int) -> None:
        super().__init__(
            f"context hard stop exceeded: limit={limit} observed={observed}"
        )
        self.limit = limit
        self.observed = observed


@dataclass(frozen=True)
class ContextItem:
    """One item kept in the rolling context."""

    role: str
    content: str
    tokens: int
    age: int = 0  # turn distance from current; 0 = newest

    def with_age(self, new_age: int) -> "ContextItem":
        return ContextItem(role=self.role, content=self.content, tokens=self.tokens, age=new_age)


@dataclass
class ContextBudgetReport:
    """Summary of the latest budget evaluation."""

    total_tokens: int
    soft_triggered: bool
    hard_triggered: bool
    safe_degraded: bool
    dropped_items: int = 0


@dataclass
class ContextBudget:
    """Context window manager with three-stage governance.

    The manager keeps an ordered list of ContextItems. On every ``apply()``
    call it computes total token usage and may:
    - Soft compaction: evict oldest non-system items until under ``soft_limit``.
    - Hard stop: when still over ``hard_stop`` raise ContextHardStopError, OR
      when ``safe_degrade`` is True replace history with a brief summary item.
    """

    soft_limit: int
    hard_stop: int
    safe_degrade: bool = False
    summarizer: Optional[Callable[[List[ContextItem]], str]] = None
    items: List[ContextItem] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.soft_limit <= 0 or self.hard_stop <= 0:
            raise ValueError("limits must be positive")
        if self.soft_limit > self.hard_stop:
            raise ValueError("soft_limit must be <= hard_stop")

    def append(self, item: ContextItem) -> None:
        """Add a new item to the front (newest)."""

        bumped = [existing.with_age(existing.age + 1) for existing in self.items]
        self.items = [item] + bumped

    def total_tokens(self) -> int:
        return sum(item.tokens for item in self.items)

    def apply(self) -> ContextBudgetReport:
        """Enforce the budget. Returns a summary report."""

        total = self.total_tokens()
        report = ContextBudgetReport(
            total_tokens=total,
            soft_triggered=False,
            hard_triggered=False,
            safe_degraded=False,
        )
        if total <= self.soft_limit:
            return report
        report.soft_triggered = True
        kept: List[ContextItem] = []
        running = 0
        for item in self.items:
            if item.role == "system":
                kept.append(item)
                running += item.tokens
                continue
            if running + item.tokens <= self.soft_limit:
                kept.append(item)
                running += item.tokens
            else:
                report.dropped_items += 1
        self.items = kept
        if running <= self.hard_stop:
            return report
        report.hard_triggered = True
        if not self.safe_degrade:
            raise ContextHardStopError(limit=self.hard_stop, observed=running)
        report.safe_degraded = True
        summary_text = (
            self.summarizer(self.items)
            if self.summarizer is not None
            else _default_summary(self.items)
        )
        budgeted_tokens = max(1, min(64, self.soft_limit // 8))
        self.items = [
            ContextItem(role="system", content=summary_text, tokens=budgeted_tokens, age=0)
        ]
        return report


def _default_summary(items: List[ContextItem]) -> str:
    return f"[summary of {len(items)} prior turns elided due to context budget]"
