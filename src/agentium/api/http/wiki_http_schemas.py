"""HTTP JSON payloads for LLM-Wiki routes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WikiSessionUploadRequest(BaseModel):
    """Body for ``POST /v1/wiki/session-uploads`` (full file as Base64)."""

    session_id: str = Field(min_length=1, max_length=128)
    filename: str = Field(min_length=1, max_length=512)
    content_base64: str = Field(
        min_length=4,
        max_length=190_000_000,
        description="Standard Base64 (RFC 4648); whitespace stripped server-side.",
    )

    model_config = ConfigDict(extra="forbid")
