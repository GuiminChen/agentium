from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.governance.access_control import (
    ABACAuthorizer,
    ABACRule,
    IAMAccessController,
    ReloadingABACAuthorizer,
)
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.models.context import RequestContext
from agentium.shared.errors import PolicyDeniedError
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


def _write_policy(tmp_path: Path) -> Path:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "version: p-ac",
                "default_decision: deny",
                "default_reason: denied by default",
                "rules:",
                "  - id: allow-read",
                "    decision: allow",
                "    reason: read allowed",
                "    tools: [read_profile]",
                "    roles: [analyst, admin]",
            ]
        ),
        encoding="utf-8",
    )
    return policy_path


def _context(role: str) -> RequestContext:
    return RequestContext(
        request_id="req-1",
        run_id="run-1",
        tenant_id="tenant-a",
        user_id="user-1",
        trace_id="trace-1",
        role=role,
        deployment_mode="prod",
    )


def test_tool_registry_denies_when_abac_rejects(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_write_policy(tmp_path))
    authorizer = ABACAuthorizer(
        rules=[
            ABACRule(
                id="allow-admin-only",
                effect="allow",
                action_patterns=["tool.execute.read_profile"],
                resource_patterns=["tool:read_profile"],
                required_roles={"admin"},
                reason="Only admin can execute",
            )
        ]
    )
    access_controller = IAMAccessController(authorization_plugin=authorizer)
    registry = ToolRegistry(
        policy_engine=engine,
        budget_ledger=BudgetLedger(
            {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
        ),
        audit_sink=InMemoryAuditSink(),
        access_controller=access_controller,
    )
    registry.register(
        ToolSpec(
            name="read_profile",
            capabilities=["read"],
            risk_level="low",
            handler=lambda args: {"ok": True},
        )
    )

    with pytest.raises(PolicyDeniedError):
        registry.execute(_context(role="analyst"), "read_profile", {})


def test_tool_registry_allows_when_abac_permits(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_write_policy(tmp_path))
    authorizer = ABACAuthorizer(
        rules=[
            ABACRule(
                id="allow-analyst",
                effect="allow",
                action_patterns=["tool.execute.read_profile"],
                resource_patterns=["tool:read_profile"],
                required_roles={"analyst"},
                reason="Analyst is allowed",
            )
        ]
    )
    access_controller = IAMAccessController(authorization_plugin=authorizer)
    registry = ToolRegistry(
        policy_engine=engine,
        budget_ledger=BudgetLedger(
            {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
        ),
        audit_sink=InMemoryAuditSink(),
        access_controller=access_controller,
    )
    registry.register(
        ToolSpec(
            name="read_profile",
            capabilities=["read"],
            risk_level="low",
            handler=lambda args: {"ok": True},
        )
    )

    result = registry.execute(_context(role="analyst"), "read_profile", {})

    assert result.output["ok"] is True


def test_tool_registry_audits_abac_reload_failure_and_rolls_back(tmp_path: Path) -> None:
    engine = PolicyEngine.load(_write_policy(tmp_path))
    abac_path = tmp_path / "abac.json"
    abac_path.write_text(
        json.dumps(
            {
                "version": "v1",
                "default_allow": False,
                "default_reason": "denied",
                "rules": [
                    {
                        "id": "allow-analyst",
                        "effect": "allow",
                        "action_patterns": ["tool.execute.read_profile"],
                        "resource_patterns": ["tool:read_profile"],
                        "required_roles": ["analyst"],
                        "reason": "Analyst allowed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    access_controller = IAMAccessController(
        authorization_plugin=ReloadingABACAuthorizer(policy_path=abac_path)
    )
    audit = InMemoryAuditSink()
    registry = ToolRegistry(
        policy_engine=engine,
        budget_ledger=BudgetLedger(
            {"tenant-a": TenantBudget(token_limit=1000, cost_limit=10.0, max_concurrency=1)}
        ),
        audit_sink=audit,
        access_controller=access_controller,
    )
    registry.register(
        ToolSpec(
            name="read_profile",
            capabilities=["read"],
            risk_level="low",
            handler=lambda args: {"ok": True},
        )
    )

    result_1 = registry.execute(_context(role="analyst"), "read_profile", {})
    assert result_1.output["ok"] is True

    time.sleep(0.01)
    abac_path.write_text("{invalid-json", encoding="utf-8")
    result_2 = registry.execute(_context(role="analyst"), "read_profile", {})
    assert result_2.output["ok"] is True

    events = audit.query(run_id="run-1")
    assert any(event.event_type == "abac_policy_reload_failed" for event in events)
