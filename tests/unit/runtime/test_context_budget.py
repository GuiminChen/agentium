"""Unit tests for ContextBudget."""

from __future__ import annotations

import pytest

from agentium.runtime.context_budget import (
    ContextBudget,
    ContextHardStopError,
    ContextItem,
)


def _budget() -> ContextBudget:
    return ContextBudget(soft_limit=100, hard_stop=200)


def test_apply_no_op_under_soft() -> None:
    budget = _budget()
    budget.append(ContextItem(role="user", content="hi", tokens=10))
    report = budget.apply()
    assert report.soft_triggered is False


def test_apply_soft_eviction_keeps_system() -> None:
    budget = _budget()
    budget.append(ContextItem(role="system", content="sys", tokens=20))
    for index in range(20):
        budget.append(ContextItem(role="user", content=f"m{index}", tokens=10))
    report = budget.apply()
    assert report.soft_triggered is True
    roles = [item.role for item in budget.items]
    assert "system" in roles


def test_apply_hard_stop_raises_without_safe_degrade() -> None:
    budget = ContextBudget(soft_limit=10, hard_stop=20)
    budget.append(ContextItem(role="system", content="sys", tokens=50))
    with pytest.raises(ContextHardStopError):
        budget.apply()


def test_apply_hard_stop_safe_degrades() -> None:
    budget = ContextBudget(soft_limit=10, hard_stop=20, safe_degrade=True)
    budget.append(ContextItem(role="system", content="sys", tokens=50))
    report = budget.apply()
    assert report.hard_triggered is True
    assert report.safe_degraded is True
    assert len(budget.items) == 1
