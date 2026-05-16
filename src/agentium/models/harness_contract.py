"""Harness contract and handoff artifact keys (P1-3 / P1-22)."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class HarnessContract(BaseModel):
    """Versioned harness checklist for long-running agent runs."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(default="v1", min_length=1)
    feature_checklist: List[str] = Field(default_factory=list)
    definition_of_done: str = Field(default="", max_length=16_000)
    evaluator_prompt_ref: Optional[str] = None
    oracle_command_ref: Optional[str] = None
    lock_resource_keys: List[str] = Field(default_factory=list)
    handoff_artifact_keys: List[str] = Field(default_factory=list)
