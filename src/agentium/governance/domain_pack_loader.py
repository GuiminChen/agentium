"""Domain pack loader: signed bundles of policy rules + sandbox profiles + DLP overrides.

A "domain pack" is the unit of governance distribution.  Each pack is a
directory containing:

```
domain_pack.yaml                # manifest (id, version, signer, checksum)
policy.yaml                     # additional PolicyRule entries
sandbox_profiles.yaml           # SafetySandbox capability allowlists
dlp_rules.yaml                  # additional DLPClassifier (label, regex, action)
```

The loader merges packs into the running configuration and refuses to apply
any pack whose declared SHA256 of ``policy.yaml`` does not match the actual
bytes.  This gives operators a coarse but reliable signing/integrity check
without requiring a real PKI for the reference backend.

Tests cover:
- happy path merge with policy + sandbox additions;
- checksum mismatch refusal;
- unknown manifest fields rejection.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from pydantic import BaseModel, Field

from agentium.governance.policy_engine import PolicyDocument, PolicyEngine, PolicyRule
from agentium.sandbox.safety_sandbox import SafetySandbox, SandboxProfile

try:
    import yaml
except ImportError:  # pragma: no cover - optional
    yaml = None


class DomainPackError(Exception):
    """Raised when a pack fails verification or has malformed structure."""


class DomainPackManifest(BaseModel):
    """Top-level manifest declared by ``domain_pack.yaml``."""

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    signer: str = Field(min_length=1)
    description: str = ""
    policy_sha256: Optional[str] = None
    sandbox_sha256: Optional[str] = None
    dlp_sha256: Optional[str] = None

    class Config:
        extra = "forbid"


@dataclass
class DomainPackLoadResult:
    """Summary of what was applied during a load."""

    pack_id: str
    pack_version: str
    rules_added: int = 0
    sandbox_profiles_added: int = 0
    dlp_rules_added: int = 0
    audit_payload: Mapping[str, Any] = field(default_factory=dict)


def _read_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None:
        raise DomainPackError("PyYAML is required to load domain packs")
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise DomainPackError(f"{path.name} must be a mapping")
    return loaded


def _sha256(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_manifest_dict(payload: Dict[str, Any]) -> DomainPackManifest:
    if hasattr(DomainPackManifest, "model_validate"):
        return DomainPackManifest.model_validate(payload)
    return DomainPackManifest.parse_obj(payload)


def verify_pack_directory_integrity(pack_dir: Path) -> DomainPackManifest:
    """Validate manifest and declared file checksums without mutating runtime policy."""

    manifest_path = pack_dir / "domain_pack.yaml"
    if not manifest_path.exists():
        raise DomainPackError(f"manifest missing: {manifest_path}")
    try:
        manifest = _parse_manifest_dict(_read_yaml(manifest_path))
    except Exception as exc:
        raise DomainPackError(f"invalid manifest {manifest_path}: {exc}") from exc

    policy_path = pack_dir / "policy.yaml"
    sandbox_path = pack_dir / "sandbox_profiles.yaml"
    dlp_path = pack_dir / "dlp_rules.yaml"

    for path, declared in (
        (policy_path, manifest.policy_sha256),
        (sandbox_path, manifest.sandbox_sha256),
        (dlp_path, manifest.dlp_sha256),
    ):
        if declared is not None:
            actual = _sha256(path)
            if actual != declared:
                raise DomainPackError(
                    f"checksum mismatch for {path.name}: declared {declared}, actual {actual}"
                )
    return manifest


class DomainPackLoader:
    """Merge a domain pack into a running PolicyEngine + SafetySandbox.

    Args:
        policy_engine: target engine; new rules are *appended*, never replacing
            the default decision.
        sandbox: target sandbox; new profiles are added per (tenant, tool).
        dlp_extender: optional callback ``(label, pattern, action)`` invoked
            for each DLP rule from the pack.  ``None`` ignores DLP rules.
        require_signer: if set, only packs whose ``signer`` is in this set are
            accepted.  Empty/``None`` disables the check (development mode).
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        sandbox: SafetySandbox,
        *,
        dlp_extender: Optional[
            "DLPRuleSink"
        ] = None,
        require_signer: Optional[List[str]] = None,
    ) -> None:
        self._engine = policy_engine
        self._sandbox = sandbox
        self._dlp_extender = dlp_extender
        self._require_signer = require_signer

    def load_directory(self, pack_dir: Path) -> DomainPackLoadResult:
        """Load and merge a single pack directory."""

        manifest_path = pack_dir / "domain_pack.yaml"
        if not manifest_path.exists():
            raise DomainPackError(f"manifest missing: {manifest_path}")
        try:
            manifest = self._parse_model(DomainPackManifest, _read_yaml(manifest_path))
        except Exception as exc:
            raise DomainPackError(f"invalid manifest {manifest_path}: {exc}") from exc

        if (
            self._require_signer
            and manifest.signer not in set(self._require_signer)
        ):
            raise DomainPackError(
                f"pack signer {manifest.signer!r} not in allowed signers"
            )

        policy_path = pack_dir / "policy.yaml"
        sandbox_path = pack_dir / "sandbox_profiles.yaml"
        dlp_path = pack_dir / "dlp_rules.yaml"

        for path, declared in (
            (policy_path, manifest.policy_sha256),
            (sandbox_path, manifest.sandbox_sha256),
            (dlp_path, manifest.dlp_sha256),
        ):
            if declared is not None:
                actual = _sha256(path)
                if actual != declared:
                    raise DomainPackError(
                        f"checksum mismatch for {path.name}: "
                        f"declared {declared}, actual {actual}"
                    )

        rules_added = self._merge_policy(policy_path)
        sandbox_added = self._merge_sandbox(sandbox_path)
        dlp_added = self._merge_dlp(dlp_path)

        return DomainPackLoadResult(
            pack_id=manifest.id,
            pack_version=manifest.version,
            rules_added=rules_added,
            sandbox_profiles_added=sandbox_added,
            dlp_rules_added=dlp_added,
            audit_payload={
                "pack_id": manifest.id,
                "version": manifest.version,
                "signer": manifest.signer,
                "policy_sha256": manifest.policy_sha256,
                "sandbox_sha256": manifest.sandbox_sha256,
            },
        )

    def _merge_policy(self, path: Path) -> int:
        if not path.exists():
            return 0
        data = _read_yaml(path)
        rules_raw = data.get("rules") or []
        if not isinstance(rules_raw, list):
            raise DomainPackError("policy.yaml `rules` must be a list")
        new_rules = [self._parse_model(PolicyRule, item) for item in rules_raw]
        document: PolicyDocument = self._engine._policy  # type: ignore[attr-defined]
        document.rules.extend(new_rules)
        return len(new_rules)

    def _merge_sandbox(self, path: Path) -> int:
        if not path.exists():
            return 0
        data = _read_yaml(path)
        profiles = data.get("profiles") or []
        added = 0
        for profile in profiles:
            try:
                tenant = str(profile["tenant_id"])
                tool = str(profile["tool_name"])
                caps = frozenset(profile.get("capabilities") or [])
                max_wall = profile.get("max_wall_seconds")
                max_bytes = profile.get("max_output_bytes")
            except KeyError as exc:
                raise DomainPackError(f"sandbox profile missing key: {exc}") from exc
            self._sandbox.register_profile(
                tenant,
                tool,
                SandboxProfile(
                    allowed_capabilities=caps,
                    max_wall_seconds=max_wall,
                    max_output_bytes=max_bytes,
                ),
            )
            added += 1
        return added

    @staticmethod
    def _parse_model(model_cls: Any, payload: Dict[str, Any]) -> Any:
        if hasattr(model_cls, "model_validate"):
            return model_cls.model_validate(payload)
        return model_cls.parse_obj(payload)

    def _merge_dlp(self, path: Path) -> int:
        if not path.exists():
            return 0
        if self._dlp_extender is None:
            return 0
        data = _read_yaml(path)
        rules_raw = data.get("rules") or []
        added = 0
        for entry in rules_raw:
            try:
                self._dlp_extender(
                    label=str(entry["label"]),
                    pattern=str(entry["pattern"]),
                    action=str(entry.get("action", "mask")),
                )
                added += 1
            except KeyError as exc:
                raise DomainPackError(f"dlp rule missing key: {exc}") from exc
        return added


class DLPRuleSink:
    """Protocol for DLP extension callbacks.  Implementations should append a
    ``(label, regex, action)`` tuple to the underlying classifier."""

    def __call__(self, *, label: str, pattern: str, action: str) -> None:  # pragma: no cover
        ...
