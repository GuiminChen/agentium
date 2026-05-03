"""Unit tests for ArtifactContract validation."""

from __future__ import annotations

from agentium.coordination.artifact_contract import ArtifactSpec, validate_artifact


def test_validate_artifact_required_keys_missing() -> None:
    spec = ArtifactSpec(name="demo", required_keys=["id", "value"])
    result = validate_artifact(spec, {"id": "x"})
    assert not result.valid
    assert result.reason is not None and "missing_required_keys" in result.reason


def test_validate_artifact_forbidden_keys() -> None:
    spec = ArtifactSpec(name="demo", forbidden_keys=["api_key"])
    result = validate_artifact(spec, {"api_key": "secret"})
    assert not result.valid
    assert result.reason is not None and "forbidden_keys_present" in result.reason


def test_validate_artifact_max_bytes() -> None:
    spec = ArtifactSpec(name="demo", max_bytes=10)
    result = validate_artifact(spec, {"big": "x" * 100})
    assert not result.valid
    assert result.reason == "artifact_exceeds_max_bytes"


def test_validate_artifact_ok() -> None:
    spec = ArtifactSpec(name="demo", required_keys=["id"])
    result = validate_artifact(spec, {"id": "x"})
    assert result.valid
    assert result.checksum_sha256 is not None
    assert result.size_bytes > 0


def test_validate_artifact_must_be_object() -> None:
    spec = ArtifactSpec(name="demo")
    result = validate_artifact(spec, [1, 2, 3])
    assert not result.valid
    assert result.reason == "artifact_not_object"
