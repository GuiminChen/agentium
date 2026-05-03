"""Artifact contract validation for inter-node workflow handoffs."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ArtifactSpec(BaseModel):
    """Declarative contract for a workflow node artifact.

    Attributes:
        name: Stable artifact name produced by node.
        required_keys: Keys that MUST be present at top level.
        forbidden_keys: Keys that MUST NOT appear (e.g. raw secrets).
        max_bytes: Hard ceiling on serialized size for backpressure.
    """

    name: str = Field(min_length=1)
    required_keys: List[str] = Field(default_factory=list)
    forbidden_keys: List[str] = Field(default_factory=list)
    max_bytes: int = Field(default=1_048_576, gt=0)

    class Config:
        extra = "forbid"


class ArtifactValidation(BaseModel):
    """Outcome of one artifact validation pass."""

    valid: bool
    reason: Optional[str] = None
    checksum_sha256: Optional[str] = None
    size_bytes: int = 0

    class Config:
        extra = "forbid"


def validate_artifact(spec: ArtifactSpec, artifact: Any) -> ArtifactValidation:
    """Validate one artifact against the spec.

    Args:
        spec: Contract spec describing the artifact.
        artifact: Candidate artifact (must be JSON-serializable).
    """

    if not isinstance(artifact, dict):
        return ArtifactValidation(valid=False, reason="artifact_not_object")
    try:
        encoded = json.dumps(artifact, sort_keys=True, ensure_ascii=False).encode("utf-8")
    except TypeError:
        return ArtifactValidation(valid=False, reason="artifact_not_serializable")
    size_bytes = len(encoded)
    if size_bytes > spec.max_bytes:
        return ArtifactValidation(
            valid=False,
            reason="artifact_exceeds_max_bytes",
            size_bytes=size_bytes,
        )
    missing: List[str] = [key for key in spec.required_keys if key not in artifact]
    if missing:
        return ArtifactValidation(
            valid=False,
            reason=f"missing_required_keys:{','.join(sorted(missing))}",
            size_bytes=size_bytes,
        )
    forbidden_present: List[str] = [
        key for key in spec.forbidden_keys if key in artifact
    ]
    if forbidden_present:
        return ArtifactValidation(
            valid=False,
            reason=f"forbidden_keys_present:{','.join(sorted(forbidden_present))}",
            size_bytes=size_bytes,
        )
    checksum = hashlib.sha256(encoded).hexdigest()
    return ArtifactValidation(
        valid=True,
        checksum_sha256=checksum,
        size_bytes=size_bytes,
    )
