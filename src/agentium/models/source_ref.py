"""Structured source anchors for research / analysis outputs (PRD §3.13, technical-design §4.1)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SourceKind = Literal["url", "api", "code_path", "dataset", "artifact", "private_tag"]
TenantScope = Literal["global", "tenant"]
ExcerptPolicy = Literal["allowed_short", "metadata_only", "forbidden"]


class SourceRef(BaseModel):
    """Minimal citation handle for `references` on turn or artifact responses."""

    model_config = ConfigDict(extra="forbid")

    ref_id: Optional[str] = None
    source_kind: SourceKind
    locator: str = Field(min_length=1)
    retrieved_at: Optional[str] = None
    content_fingerprint: Optional[str] = None
    tenant_scope: Optional[TenantScope] = None
    excerpt_policy: Optional[ExcerptPolicy] = None


__all__ = ["ExcerptPolicy", "SourceKind", "SourceRef", "TenantScope"]
