"""Run manifest snapshot models aligned with PRD §3.16 / §3.18."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class RunManifest(BaseModel):
    """Immutable-friendly description of a backend run / deployment snapshot.

    Attributes:
        profile: Active runtime profile (dev, staging, prod).
        build_id: Build or release identifier for traceability.
        policy_bundle_ref: Optional pointer to pinned policy artifact.
        feature_flags: Optional feature flag map for reproducibility.
        declared_tools: Optional list of tool names allowed for this run (enforced in ToolRegistry).
    """

    profile: str = Field(min_length=1)
    build_id: str = Field(min_length=1)
    policy_bundle_ref: Optional[str] = None
    feature_flags: Dict[str, Any] = Field(default_factory=dict)
    declared_tools: Optional[List[str]] = None

    class Config:
        extra = "forbid"

    @field_validator("declared_tools", mode="before")
    @classmethod
    def _strip_declared_tools(cls, v: Any) -> Any:
        if v is None:
            return None
        if not isinstance(v, list):
            raise ValueError("declared_tools must be a list of strings or null")
        out = [str(x).strip() for x in v if str(x).strip()]
        return out

    def __init__(self, **data: Any) -> None:
        profile = data.get("profile")
        if isinstance(profile, str):
            data["profile"] = profile.strip().lower()
        super().__init__(**data)

    def content_sha256(self) -> str:
        """Stable SHA-256 over canonical JSON for audit and gate checks."""

        if hasattr(self, "model_dump"):
            payload = self.model_dump(mode="json", exclude_none=True)
        else:
            payload = self.dict(exclude_none=True)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RunManifestPolicy(BaseModel):
    """Server-side constraints for accepting inbound run manifests."""

    expected_profile: str = Field(min_length=1)
    expected_sha256: Optional[str] = Field(default=None, min_length=1)
    require_manifest: bool = False

    class Config:
        extra = "forbid"

    def validate_manifest(self, manifest: Optional[RunManifest]) -> tuple[Optional[RunManifest], Optional[str]]:
        """Return (manifest, error_code) where error_code is set on violation."""

        if manifest is None:
            if self.require_manifest:
                return None, "run_manifest_required"
            return None, None
        if manifest.profile != self.expected_profile:
            return None, "run_manifest_profile_mismatch"
        digest = manifest.content_sha256()
        if self.expected_sha256 is not None and digest != self.expected_sha256:
            return None, "run_manifest_sha_mismatch"
        return manifest, None


def parse_run_manifest_payload(raw: Optional[Dict[str, Any]]) -> tuple[Optional[RunManifest], Optional[str]]:
    """Parse arbitrary JSON object into RunManifest or return error code."""

    if raw is None:
        return None, None
    try:
        if hasattr(RunManifest, "model_validate"):
            return RunManifest.model_validate(raw), None
        return RunManifest.parse_obj(raw), None
    except Exception:
        return None, "run_manifest_invalid"
