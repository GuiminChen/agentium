"""Tests for skill_run / skill_invoke builtin tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers.app_settings_test_baseline import app_settings_extended_dict_for_data_dir
from tests.helpers.chat_ingress_test_defaults import chat_ingress_off_fields

from agentium.app.settings import AppSettings
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.models.context import RequestContext
from agentium.shared.errors import PolicyDeniedError
from agentium.shared.request_context import set_request_context
from agentium.tools.builtin_skill import register_skill_tools
from agentium.tools.tool_registry import ToolRegistry
from agentium.sandbox.safety_sandbox import SafetySandbox, SandboxProfile


def _policy_allow_skills(tmp_path: Path) -> Path:
    p = tmp_path / "pol.yaml"
    p.write_text(
        "\n".join(
            [
                "version: t",
                "default_decision: deny",
                "default_reason: denied",
                "rules:",
                "  - id: tools",
                "    decision: allow",
                "    tools: [skill_run, skill_invoke]",
                "    reason: tools",
                "    roles: [user]",
                "  - id: sk",
                "    decision: allow",
                "    skills: ['*']",
                "    skill_script_paths: ['*']",
                "    reason: skills",
                "    roles: [user]",
            ]
        ),
        encoding="utf-8",
    )
    return p


def _minimal_settings(tmp_path: Path, repo: Path, skills: Path) -> AppSettings:
    plugins = tmp_path / "plugins.yaml"
    plugins.write_text(
        "orchestration:\n  backend: native\nmemory:\n  backend: memory\n"
        "evolution:\n  plugin: native\n",
        encoding="utf-8",
    )
    from agentium.app.plugins_config import load_plugins_config

    pol = _policy_allow_skills(tmp_path)
    us = tmp_path / "usr_skills"
    us.mkdir()
    return AppSettings(
        profile="dev",  # type: ignore[arg-type]
        host="127.0.0.1",
        port=8765,
        policy_path=pol,
        data_dir=tmp_path,
        plugins_config_path=plugins,
        plugins=load_plugins_config(plugins),
        approval_backend="memory",
        audit_backend="memory",
        identity_mode="hybrid",
        require_run_manifest=False,
        expected_run_manifest_sha256=None,
        background_enabled=False,
        background_interval_seconds=30.0,
        background_noise_rps_pause=0.0,
        telemetry_mode="null",
        default_tenant_token_limit=100,
        default_tenant_cost_limit=1.0,
        default_tenant_max_concurrency=2,
        sqlite_approval_ttl_seconds=None,
        emergence_node_warn=1,
        emergence_node_hard=2,
        emergence_outbound_warn=1,
        emergence_outbound_hard=2,
        outbound_rate_limit_per_minute=60,
        policy_release_hmac_secret=None,
        grafana_base_url=None,
        tempo_base_url=None,
        domain_packs_root=None,
        repo_root=repo,
        skills_project_root=skills,
        skills_user_root=us,
        skills_config_root=None,
        oidc_issuer_configs=(),
        lsp_upstream_url=None,
        deepseek_api_key=None,
        deepseek_base_url="https://api.deepseek.com",
        chat_completion_model="deepseek-v4-flash",
        chat_completion_timeout_seconds=120.0,
        chat_skill_body_max_chars=8000,
        chat_agent_tools_enabled=False,
        chat_agent_max_tool_rounds=8,
        chat_mid_semantic_memory_enabled=True,
        chat_session_running_summary_enabled=True,
        workspace_agent_persona_max_chars=4096,
        workspace_agent_max_skill_tags=8,
        workspace_agent_max_tool_allowlist=24,
        deepseek_thinking_enabled=True,
        deepseek_reasoning_effort="high",
        deepseek_inject_think_max_instruction=True,
        deepseek_dsml_tool_prompt_enabled=True,
        persona_templates_extra_root=None,
        log_file_path=None,
        log_file_backup_count=14,
        log_to_console=True,
        chat_auto_session_title_enabled=False,
        deferred_tasks_enabled=False,
        deferred_thread_pool_size=4,
        deferred_task_backend="thread",
        redis_url=None,
        **app_settings_extended_dict_for_data_dir(tmp_path),
        **chat_ingress_off_fields(tmp_path),
    )


def _ctx() -> RequestContext:
    return RequestContext(
        request_id="r1",
        run_id="run1",
        tenant_id="t1",
        user_id="u1",
        trace_id="tr1",
        role="user",
        deployment_mode="dev",
    )


def test_skill_run_returns_body(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    sk = repo / "skills"
    sk.mkdir(parents=True)
    (sk / "demo").mkdir()
    (sk / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: hello world skill\n---\n\n# Body\n",
        encoding="utf-8",
    )
    settings = _minimal_settings(tmp_path, repo, sk)
    pe = PolicyEngine.load(settings.policy_path)
    reg = ToolRegistry(
        policy_engine=pe,
        budget_ledger=BudgetLedger({}, default_budget=TenantBudget(1000, 10.0, 8)),
        audit_sink=InMemoryAuditSink(),
        approval_gate=ApprovalGate(),
    )
    sb = SafetySandbox()
    sb.register_profile(
        "*",
        "skill_invoke",
        SandboxProfile(
            allowed_capabilities=frozenset(["skill.subprocess"]),
            max_wall_seconds=60.0,
            max_output_bytes=500_000,
        ),
    )
    register_skill_tools(reg, settings, pe, sb)

    set_request_context(_ctx())
    out = reg.execute(_ctx(), "skill_run", {"query": "hello world skill"})
    payload = out.output
    assert payload["ok"] is True
    assert payload["primary_skill_id"] == "demo"
    assert "# Body" in payload["skill_body"]


def test_skill_run_policy_denied(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    sk = repo / "skills"
    sk.mkdir(parents=True)
    (sk / "demo").mkdir()
    (sk / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: secret skill xyzzy\n---\n\n# X\n",
        encoding="utf-8",
    )
    plugins = tmp_path / "plugins.yaml"
    plugins.write_text(
        "orchestration:\n  backend: native\nmemory:\n  backend: memory\n"
        "evolution:\n  plugin: native\n",
        encoding="utf-8",
    )
    from agentium.app.plugins_config import load_plugins_config

    pol = tmp_path / "pol2.yaml"
    pol.write_text(
        "\n".join(
            [
                "version: t",
                "default_decision: deny",
                "default_reason: 'no'",
                "rules:",
                "  - id: tools",
                "    decision: allow",
                "    reason: allow tools",
                "    tools: [skill_run]",
                "    roles: [user]",
                "  - id: only-other",
                "    decision: allow",
                "    reason: only other skill",
                "    skills: [other]",
                "    roles: [user]",
            ]
        ),
        encoding="utf-8",
    )
    us = tmp_path / "usr_skills"
    us.mkdir()
    settings = AppSettings(
        profile="dev",  # type: ignore[arg-type]
        host="127.0.0.1",
        port=8765,
        policy_path=pol,
        data_dir=tmp_path,
        plugins_config_path=plugins,
        plugins=load_plugins_config(plugins),
        approval_backend="memory",
        audit_backend="memory",
        identity_mode="hybrid",
        require_run_manifest=False,
        expected_run_manifest_sha256=None,
        background_enabled=False,
        background_interval_seconds=30.0,
        background_noise_rps_pause=0.0,
        telemetry_mode="null",
        default_tenant_token_limit=100,
        default_tenant_cost_limit=1.0,
        default_tenant_max_concurrency=2,
        sqlite_approval_ttl_seconds=None,
        emergence_node_warn=1,
        emergence_node_hard=2,
        emergence_outbound_warn=1,
        emergence_outbound_hard=2,
        outbound_rate_limit_per_minute=60,
        policy_release_hmac_secret=None,
        grafana_base_url=None,
        tempo_base_url=None,
        domain_packs_root=None,
        repo_root=repo,
        skills_project_root=sk,
        skills_user_root=us,
        skills_config_root=None,
        oidc_issuer_configs=(),
        lsp_upstream_url=None,
        deepseek_api_key=None,
        deepseek_base_url="https://api.deepseek.com",
        chat_completion_model="deepseek-v4-flash",
        chat_completion_timeout_seconds=120.0,
        chat_skill_body_max_chars=8000,
        chat_agent_tools_enabled=False,
        chat_agent_max_tool_rounds=8,
        chat_mid_semantic_memory_enabled=True,
        chat_session_running_summary_enabled=True,
        workspace_agent_persona_max_chars=4096,
        workspace_agent_max_skill_tags=8,
        workspace_agent_max_tool_allowlist=24,
        deepseek_thinking_enabled=True,
        deepseek_reasoning_effort="high",
        deepseek_inject_think_max_instruction=True,
        deepseek_dsml_tool_prompt_enabled=True,
        persona_templates_extra_root=None,
        log_file_path=None,
        log_file_backup_count=14,
        log_to_console=True,
        chat_auto_session_title_enabled=False,
        deferred_tasks_enabled=False,
        deferred_thread_pool_size=4,
        deferred_task_backend="thread",
        redis_url=None,
        **app_settings_extended_dict_for_data_dir(tmp_path),
        **chat_ingress_off_fields(tmp_path),
    )
    pe = PolicyEngine.load(pol)
    reg = ToolRegistry(
        policy_engine=pe,
        budget_ledger=BudgetLedger({}, default_budget=TenantBudget(1000, 10.0, 8)),
        audit_sink=InMemoryAuditSink(),
        approval_gate=ApprovalGate(),
    )
    register_skill_tools(reg, settings, pe, SafetySandbox())

    set_request_context(_ctx())
    with pytest.raises(PolicyDeniedError):
        reg.execute(_ctx(), "skill_run", {"query": "secret skill xyzzy"})


def test_skill_invoke_runs_allowlisted_script(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    sk = repo / "skills"
    pkg = sk / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "SKILL.md").write_text(
        "---\nname: pkg\ndescription: pkg\n---\n",
        encoding="utf-8",
    )
    scripts = pkg / "scripts"
    scripts.mkdir()
    (scripts / "hi.py").write_text("print('hi')", encoding="utf-8")
    (pkg / "agentium_script_allowlist.txt").write_text("scripts/hi.py\n", encoding="utf-8")

    settings = _minimal_settings(tmp_path, repo, sk)
    pe = PolicyEngine.load(settings.policy_path)
    reg = ToolRegistry(
        policy_engine=pe,
        budget_ledger=BudgetLedger({}, default_budget=TenantBudget(1000, 10.0, 8)),
        audit_sink=InMemoryAuditSink(),
        approval_gate=ApprovalGate(),
    )
    sb = SafetySandbox()
    sb.register_profile(
        "*",
        "skill_invoke",
        SandboxProfile(
            allowed_capabilities=frozenset(["skill.subprocess"]),
            max_wall_seconds=60.0,
            max_output_bytes=500_000,
        ),
    )
    register_skill_tools(reg, settings, pe, sb)

    set_request_context(_ctx())
    out = reg.execute(
        _ctx(),
        "skill_invoke",
        {"skill_id": "pkg", "script": "scripts/hi.py", "script_argv": []},
    )
    assert out.output["ok"] is True
    assert out.output["returncode"] == 0
    assert "hi" in out.output["stdout"]
