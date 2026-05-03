"""Declarative policy engine for tool-level governance decisions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field

from agentium.models.context import Decision, DecisionType, RequestContext
from agentium.shared.errors import ConfigurationError

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency at runtime
    yaml = None


class PolicyRule(BaseModel):
    """Declarative matching rule used by PolicyEngine.

    Attributes:
        id: Stable rule id for audit and explainability.
        decision: Decision type applied when rule matches.
        reason: Human-readable reason to return with decision.
        tools: Optional set of tool names to match (required for ``decide_tool_call``).
        skills: Optional set of skill ids for ``decide_skill_use`` / ``decide_skill_script``.
            Use ``*`` to match any skill id.
        skill_script_paths: Relative paths (POSIX, e.g. ``scripts/foo.py``) allowed for
            ``decide_skill_script`` when non-empty. Use ``*`` to allow any path that
            passes the skill package allowlist file.
        roles: Optional set of caller roles to match.
        tenants: Optional set of tenant ids to match.
    """

    id: str = Field(min_length=1)
    decision: DecisionType
    reason: str = Field(min_length=1)
    tools: Set[str] = Field(default_factory=set)
    skills: Set[str] = Field(default_factory=set)
    skill_script_paths: Set[str] = Field(default_factory=set)
    roles: Set[str] = Field(default_factory=set)
    tenants: Set[str] = Field(default_factory=set)

    class Config:
        """Pydantic model configuration."""

        extra = "forbid"

    def matches(self, context: RequestContext, tool_name: str) -> bool:
        """Return True when this rule matches a **tool** invocation."""

        if not self.tools:
            return False
        if tool_name not in self.tools:
            return False
        if self.roles and context.role not in self.roles:
            return False
        if self.tenants and context.tenant_id not in self.tenants:
            return False
        return True

    def matches_skill_use(self, context: RequestContext, skill_id: str) -> bool:
        """Match loading materialized content for ``skill_id``."""

        if not self.skills:
            return False
        if self.roles and context.role not in self.roles:
            return False
        if self.tenants and context.tenant_id not in self.tenants:
            return False
        return "*" in self.skills or skill_id in self.skills

    def matches_skill_script(self, context: RequestContext, skill_id: str, relpath: str) -> bool:
        """Match script execution under ``skill_id`` at relative path ``relpath``."""

        if not self.skills or not self.skill_script_paths:
            return False
        if self.roles and context.role not in self.roles:
            return False
        if self.tenants and context.tenant_id not in self.tenants:
            return False
        if not ("*" in self.skills or skill_id in self.skills):
            return False
        npath = relpath.replace("\\", "/").strip().lstrip("./")
        return "*" in self.skill_script_paths or npath in self.skill_script_paths


class PolicyDocument(BaseModel):
    """Policy document loaded from YAML/JSON file."""

    version: str = Field(default="v1", min_length=1)
    default_decision: DecisionType = DecisionType.DENY
    default_reason: str = Field(default="Denied by default policy", min_length=1)
    rules: List[PolicyRule] = Field(default_factory=list)

    class Config:
        """Pydantic model configuration."""

        extra = "forbid"


class PolicyEngine:
    """Evaluate declarative rules for tool invocation governance."""

    def __init__(self, policy: PolicyDocument) -> None:
        self._policy = policy

    @property
    def version(self) -> str:
        """Return active policy version string."""

        return self._policy.version

    @classmethod
    def load(cls, policy_path: Path) -> PolicyEngine:
        """Load policy engine from YAML/JSON file.

        Args:
            policy_path: Path to policy file.

        Returns:
            PolicyEngine: Initialized policy engine.

        Raises:
            ConfigurationError: If policy file is invalid or unsupported.
        """

        if not policy_path.exists():
            raise ConfigurationError(f"Policy file does not exist: {policy_path}")
        raw = cls._load_raw(policy_path)
        document = PolicyDocument.parse_obj(raw)
        return cls(policy=document)

    @staticmethod
    def _load_raw(policy_path: Path) -> Dict[str, Any]:
        suffix = policy_path.suffix.lower()
        text = policy_path.read_text(encoding="utf-8")
        if suffix == ".json":
            return json.loads(text)
        if suffix in {".yaml", ".yml"}:
            if yaml is None:
                raise ConfigurationError(
                    "PyYAML is required for YAML policies. Install `pyyaml`."
                )
            loaded = yaml.safe_load(text)
            if not isinstance(loaded, dict):
                raise ConfigurationError("YAML policy root must be a mapping")
            return loaded
        raise ConfigurationError("Unsupported policy file extension. Use JSON or YAML")

    def decide_tool_call(
        self,
        context: RequestContext,
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
    ) -> Decision:
        """Decide whether a tool call is allowed under current policy.

        Args:
            context: Request context for rule matching.
            tool_name: Candidate tool name.
            args: Optional tool arguments for future rule extensions.

        Returns:
            Decision: Structured allow/deny/approval decision.
        """

        _ = args
        for rule in self._policy.rules:
            if rule.matches(context=context, tool_name=tool_name):
                return Decision(
                    decision=rule.decision,
                    reason=rule.reason,
                    rule_id=rule.id,
                )
        return Decision(
            decision=self._policy.default_decision,
            reason=self._policy.default_reason,
            rule_id=None,
        )

    def decide_skill_use(self, context: RequestContext, skill_id: str) -> Decision:
        """Allow/deny loading skill instructions (e.g. ``skill_run`` body)."""

        for rule in self._policy.rules:
            if rule.matches_skill_use(context=context, skill_id=skill_id):
                return Decision(
                    decision=rule.decision,
                    reason=rule.reason,
                    rule_id=rule.id,
                )
        return Decision(
            decision=self._policy.default_decision,
            reason=self._policy.default_reason,
            rule_id=None,
        )

    def decide_skill_script(
        self, context: RequestContext, skill_id: str, script_relpath: str
    ) -> Decision:
        """Allow/deny executing a script file relative to the skill package root."""

        for rule in self._policy.rules:
            if rule.matches_skill_script(
                context=context, skill_id=skill_id, relpath=script_relpath
            ):
                return Decision(
                    decision=rule.decision,
                    reason=rule.reason,
                    rule_id=rule.id,
                )
        return Decision(
            decision=self._policy.default_decision,
            reason=self._policy.default_reason,
            rule_id=None,
        )

    def summarize_for_http(self) -> Dict[str, Any]:
        """Return a read-only policy summary for the HTTP control plane (PRD G-04)."""

        rules = self._policy.rules
        decisions_present = sorted({r.decision.value for r in rules})
        return {
            "version": self._policy.version,
            "default_decision": self._policy.default_decision.value,
            "rules_summary": {
                "rule_count": len(rules),
                "decisions_present": decisions_present,
            },
            "matched_hint": (
                "Snapshot of the loaded policy bundle only. "
                "Per-request outcomes appear in audit events (e.g. policy_decision)."
            ),
            "rules": [
                {
                    "id": rule.id,
                    "decision": rule.decision.value,
                    "reason": rule.reason,
                    "tools": sorted(rule.tools),
                    "skills": sorted(rule.skills),
                    "skill_script_paths": sorted(rule.skill_script_paths),
                    "roles": sorted(rule.roles),
                    "tenants": sorted(rule.tenants),
                }
                for rule in rules
            ],
        }
