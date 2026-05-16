"""Tool contract spec for ACI/ACE compliance and registry gating."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class FailureSemantic(BaseModel):
    """Declarative failure semantics for a tool."""

    retryable: bool = False
    idempotent: bool = False
    requires_compensation: bool = False
    failure_codes: List[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"


class ToolContract(BaseModel):
    """Static contract attached to every registered tool.

    Attributes:
        name: Stable tool name (must match ToolSpec.name).
        version: Contract version, used for telemetry and policy bindings.
        description: Human-readable description for ACI/ACE catalog.
        input_schema: JSON Schema fragment validating tool args.
        output_schema: JSON Schema fragment validating tool output.
        failure_semantics: Failure-handling guarantees.
        idempotency_key_args: Args fields used to derive idempotency key.
        examples: At least one positive example for ACI/ACE catalog.
    """

    name: str = Field(min_length=1)
    version: str = Field(default="v1", min_length=1)
    description: str = Field(min_length=1)
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    failure_semantics: FailureSemantic = Field(default_factory=FailureSemantic)
    idempotency_key_args: List[str] = Field(default_factory=list)
    examples: List[Dict[str, Any]] = Field(default_factory=list)

    class Config:
        extra = "forbid"


class ToolContractError(ValueError):
    """Raised when a tool contract is missing or invalid."""


def assert_contract_valid(
    contract: Optional[ToolContract],
    tool_name: str,
    *,
    min_description_chars: int = 12,
) -> None:
    """Raise ToolContractError when contract is missing or violates ACI gate."""

    if contract is None:
        raise ToolContractError(f"tool_contract_missing:{tool_name}")
    if contract.name != tool_name:
        raise ToolContractError(
            f"tool_contract_name_mismatch:{tool_name}!={contract.name}"
        )
    stripped = contract.description.strip()
    if not stripped:
        raise ToolContractError(f"tool_contract_description_empty:{tool_name}")
    if len(stripped) < max(1, int(min_description_chars)):
        raise ToolContractError(
            f"tool_contract_description_too_short:{tool_name}:"
            f"{len(stripped)}<{max(1, int(min_description_chars))}"
        )
    if not contract.examples:
        raise ToolContractError(f"tool_contract_missing_examples:{tool_name}")
