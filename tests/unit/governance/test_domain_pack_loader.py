"""Unit tests for DomainPackLoader: signing, merge, and rejection paths."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agentium.governance.domain_pack_loader import (
    DomainPackError,
    DomainPackLoader,
)
from agentium.governance.policy_engine import PolicyDocument, PolicyEngine
from agentium.models.context import DecisionType
from agentium.sandbox.safety_sandbox import SafetySandbox


def _write_pack(
    pack_dir: Path,
    *,
    policy_yaml: str = "rules: []\n",
    sandbox_yaml: str = "profiles: []\n",
    dlp_yaml: str = "rules: []\n",
    declare_policy_sha: bool = True,
    declare_sandbox_sha: bool = True,
    signer: str = "agentium",
):
    pack_dir.mkdir(parents=True, exist_ok=True)
    policy_path = pack_dir / "policy.yaml"
    sandbox_path = pack_dir / "sandbox_profiles.yaml"
    dlp_path = pack_dir / "dlp_rules.yaml"
    policy_path.write_bytes(policy_yaml.encode("utf-8"))
    sandbox_path.write_bytes(sandbox_yaml.encode("utf-8"))
    dlp_path.write_bytes(dlp_yaml.encode("utf-8"))
    manifest = ["id: pack-a", "version: '1.0'", f"signer: {signer}"]
    if declare_policy_sha:
        sha = hashlib.sha256(policy_path.read_bytes()).hexdigest()
        manifest.append(f"policy_sha256: {sha}")
    if declare_sandbox_sha:
        sha = hashlib.sha256(sandbox_path.read_bytes()).hexdigest()
        manifest.append(f"sandbox_sha256: {sha}")
    (pack_dir / "domain_pack.yaml").write_bytes(("\n".join(manifest) + "\n").encode("utf-8"))


def _engine() -> PolicyEngine:
    return PolicyEngine(
        policy=PolicyDocument(
            version="v1",
            default_decision=DecisionType.DENY,
            default_reason="default",
        )
    )


def test_domain_pack_loader_merges_policy_and_sandbox(tmp_path: Path):
    pack_dir = tmp_path / "pack"
    policy_yaml = (
        "rules:\n"
        "  - id: allow-domain-tool\n"
        "    decision: allow\n"
        "    reason: domain pack allowed echo\n"
        "    tools: [echo]\n"
    )
    sandbox_yaml = (
        "profiles:\n"
        "  - tenant_id: tenant-a\n"
        "    tool_name: echo\n"
        "    capabilities: [compute]\n"
    )
    _write_pack(pack_dir, policy_yaml=policy_yaml, sandbox_yaml=sandbox_yaml)
    engine = _engine()
    sandbox = SafetySandbox()
    loader = DomainPackLoader(engine, sandbox)

    result = loader.load_directory(pack_dir)
    assert result.rules_added == 1
    assert result.sandbox_profiles_added == 1
    rules = engine._policy.rules  # type: ignore[attr-defined]
    assert any(r.id == "allow-domain-tool" for r in rules)
    profile = sandbox.resolve_profile("tenant-a", "echo")
    assert "compute" in profile.allowed_capabilities


def test_domain_pack_loader_rejects_checksum_mismatch(tmp_path: Path):
    pack_dir = tmp_path / "pack"
    _write_pack(pack_dir, policy_yaml="rules: []\n")
    (pack_dir / "policy.yaml").write_text("rules:\n  - id: tampered\n", encoding="utf-8")
    loader = DomainPackLoader(_engine(), SafetySandbox())
    with pytest.raises(DomainPackError):
        loader.load_directory(pack_dir)


def test_domain_pack_loader_enforces_signer_allowlist(tmp_path: Path):
    pack_dir = tmp_path / "pack"
    _write_pack(pack_dir, signer="anonymous")
    loader = DomainPackLoader(
        _engine(), SafetySandbox(), require_signer=["agentium-internal"]
    )
    with pytest.raises(DomainPackError):
        loader.load_directory(pack_dir)


def test_domain_pack_loader_invokes_dlp_extender(tmp_path: Path):
    pack_dir = tmp_path / "pack"
    dlp_yaml = (
        "rules:\n"
        "  - label: custom-secret\n"
        "    pattern: 'XYZ-\\d+'\n"
        "    action: block\n"
    )
    _write_pack(pack_dir, dlp_yaml=dlp_yaml)
    sink_calls = []

    def sink(*, label: str, pattern: str, action: str) -> None:
        sink_calls.append((label, pattern, action))

    loader = DomainPackLoader(_engine(), SafetySandbox(), dlp_extender=sink)
    result = loader.load_directory(pack_dir)
    assert result.dlp_rules_added == 1
    assert sink_calls == [("custom-secret", "XYZ-\\d+", "block")]
