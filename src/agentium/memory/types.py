"""Shared memory record models and enums."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict

from pydantic import BaseModel, Field


class MemoryLayer(str, Enum):
    """Three-tier memory model: short / mid / long."""

    SHORT = "short"
    MID = "mid"
    LONG = "long"


class MemoryRecord(BaseModel):
    """One memory record bound to a tenant and layer."""

    tenant_id: str = Field(min_length=1)
    layer: MemoryLayer
    key: str = Field(min_length=1)
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        extra = "forbid"
