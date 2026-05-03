from __future__ import annotations

import json
from pathlib import Path

from agentium.governance.policy_engine import PolicyEngine
from agentium.models.context import DecisionType, RequestContext


def _context(role: str = "user", tenant_id: str = "tenant-a") -> RequestContext:
    return RequestContext(
        request_id="req-1",
        run_id="run-1",
        tenant_id=tenant_id,
        user_id="user-1",
        trace_id="trace-1",
        role=role,
        deployment_mode="prod",
    )


def test_policy_engine_matches_specific_rule(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: deny",
                "default_reason: denied by default",
                "rules:",
                "  - id: allow-search",
                "    decision: allow",
                "    reason: allowed for search",
                "    tools: [web_search]",
                "    roles: [analyst]",
                "    tenants: [tenant-a]",
            ]
        ),
        encoding="utf-8",
    )
    engine = PolicyEngine.load(policy_path)
    decision = engine.decide_tool_call(_context(role="analyst"), "web_search", {})

    assert engine.version == "p0"
    assert decision.decision == DecisionType.ALLOW
    assert decision.rule_id == "allow-search"


def test_policy_engine_falls_back_to_default_decision(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: deny",
                "default_reason: denied by default",
                "rules: []",
            ]
        ),
        encoding="utf-8",
    )
    engine = PolicyEngine.load(policy_path)
    decision = engine.decide_tool_call(_context(), "db_export", {})

    assert decision.decision == DecisionType.DENY
    assert decision.reason == "denied by default"
    assert decision.rule_id is None


def test_policy_engine_supports_json_documents(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "version": "p1",
                "default_decision": "deny",
                "default_reason": "denied by default",
                "rules": [
                    {
                        "id": "approval-for-export",
                        "decision": "require_approval",
                        "reason": "high risk export",
                        "tools": ["db_export"],
                        "roles": ["admin"],
                        "tenants": ["tenant-a"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    engine = PolicyEngine.load(policy_path)
    decision = engine.decide_tool_call(_context(role="admin"), "db_export", {})

    assert decision.decision == DecisionType.REQUIRE_APPROVAL
    assert decision.rule_id == "approval-for-export"


def test_policy_engine_skill_use_wildcard(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: deny",
                "default_reason: denied",
                "rules:",
                "  - id: allow-skills",
                "    decision: allow",
                "    reason: ok",
                "    skills: ['*']",
                "    roles: [user]",
            ]
        ),
        encoding="utf-8",
    )
    engine = PolicyEngine.load(policy_path)
    d = engine.decide_skill_use(_context(role="user"), "docx")
    assert d.decision == DecisionType.ALLOW

    d2 = engine.decide_skill_use(_context(role="other"), "docx")
    assert d2.decision == DecisionType.DENY


def test_policy_engine_tool_rule_with_only_skills_does_not_match_tools(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: deny",
                "default_reason: denied",
                "rules:",
                "  - id: skills-only",
                "    decision: allow",
                "    reason: skill",
                "    skills: [x]",
                "    roles: [user]",
            ]
        ),
        encoding="utf-8",
    )
    engine = PolicyEngine.load(policy_path)
    d = engine.decide_tool_call(_context(role="user"), "echo_tool", {})
    assert d.decision == DecisionType.DENY


def test_policy_engine_skill_script_rule(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: deny",
                "default_reason: denied",
                "rules:",
                "  - id: script-ok",
                "    decision: allow",
                "    reason: script",
                "    skills: [my]",
                "    skill_script_paths: [scripts/hi.py]",
                "    roles: [admin]",
            ]
        ),
        encoding="utf-8",
    )
    engine = PolicyEngine.load(policy_path)
    ok = engine.decide_skill_script(_context(role="admin"), "my", "scripts/hi.py")
    assert ok.decision == DecisionType.ALLOW
    bad = engine.decide_skill_script(_context(role="admin"), "my", "other.py")
    assert bad.decision == DecisionType.DENY
