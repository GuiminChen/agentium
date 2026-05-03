"""Project-level custom exceptions."""

from __future__ import annotations

from typing import Optional


class AgentiumError(Exception):
    """Base exception for Agentium."""


class ConfigurationError(AgentiumError):
    """Raised when required configuration is missing."""


class PolicyDeniedError(AgentiumError):
    """Raised when policy denies a tool call."""


class ApprovalRequiredError(AgentiumError):
    """Raised when policy requires approval before execution."""

    def __init__(self, message: str, approval_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.approval_id = approval_id


class BudgetExceededError(AgentiumError):
    """Raised when budget reservation fails."""
