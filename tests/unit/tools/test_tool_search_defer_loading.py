"""P1-26: tool_search ranking and defer_loading payload shaping."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers.app_settings_test_baseline import app_settings_extended_dict_for_data_dir
from tests.helpers.chat_ingress_test_defaults import chat_ingress_off_fields

from agentium.app.plugins_config import load_plugins_config
from agentium.app.settings import AppSettings
from agentium.coordination.chat_agent_tool_loop import (
    build_openai_tools_for_chat_loop,
    eligible_base_chat_tool_names,
)
from agentium.governance.approval_gate import ApprovalGate
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
from agentium.tools.tool_registry import ToolRegistry, ToolSpec
from agentium.tools.tool_search_index import rank_tool_rows


def _settings(tmp_path: Path, *, defer_threshold: int = 24, max_expose: int = 8) -> AppSettings:
    repo = tmp_path / "r"
    repo.mkdir()
    plugins = tmp_path / "p.yaml"
    plugins.write_text(
        "orchestration:\n  backend: native\nmemory:\n  backend: memory\n"
        "evolution:\n  plugin: native\n",
        encoding="utf-8",
    )
    pol = tmp_path / "pol.yaml"
    pol.write_text("version: t\ndefault_decision: allow\ndefault_reason: x\nrules: []\n", encoding="utf-8")
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
        skills_project_root=None,
        skills_user_root=tmp_path / "usk",
        skills_config_root=None,
        oidc_issuer_configs=(),
        lsp_upstream_url=None,
        deepseek_api_key=None,
        deepseek_base_url="https://api.deepseek.com",
        chat_completion_model="deepseek-v4-flash",
        chat_completion_timeout_seconds=120.0,
        chat_skill_body_max_chars=8000,
        chat_agent_tools_enabled=True,
        chat_agent_max_tool_rounds=8,
        chat_mid_semantic_memory_enabled=False,
        chat_session_running_summary_enabled=False,
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
        log_to_console=False,
        chat_auto_session_title_enabled=False,
        deferred_tasks_enabled=False,
        deferred_thread_pool_size=4,
        deferred_task_backend="thread",
        redis_url=None,
        **{
            **app_settings_extended_dict_for_data_dir(tmp_path),
            "chat_tool_defer_loading_threshold": defer_threshold,
            "chat_tool_search_max_expose": max_expose,
        },
        **chat_ingress_off_fields(tmp_path),
    )


def _many_low_tools_registry() -> ToolRegistry:
    audit = InMemoryAuditSink()
    pol = Path(__file__).resolve().parents[3] / "configs" / "runtime_policy.default.yaml"
    pe = PolicyEngine.load(pol)
    ledger = BudgetLedger({"t": TenantBudget(10_000, 10.0, 4)})
    reg = ToolRegistry(policy_engine=pe, budget_ledger=ledger, audit_sink=audit, approval_gate=ApprovalGate())
    for i in range(30):
        reg.register(
            ToolSpec(
                name=f"z_defer_tool_{i:02d}",
                capabilities=["test"],
                risk_level="low",
                handler=lambda a, _i=i: {"i": _i},
            )
        )
    reg.register_tool_search_meta()
    return reg


def test_rank_tool_rows_orders_by_overlap() -> None:
    rows = [
        ("alpha", "hash digest", "alpha hash digest"),
        ("beta", "word count", "beta word count"),
        ("gamma", "nothing here", "gamma nothing here"),
    ]
    hits = rank_tool_rows(rows, query="hash digest workflow", limit=2)
    assert hits[0].name == "alpha"
    assert hits[0].score >= hits[1].score


def test_defer_payload_includes_tool_search_and_bounded_schemas(tmp_path: Path) -> None:
    reg = _many_low_tools_registry()
    settings = _settings(tmp_path, defer_threshold=10, max_expose=4)
    names = eligible_base_chat_tool_names(reg, None)
    assert len(names) == 30
    payload, defer, exposed = build_openai_tools_for_chat_loop(reg, settings, None, deferred_exposed=None)
    assert defer is True
    assert len(exposed) <= 4
    fnames = [t["function"]["name"] for t in payload]
    assert "tool_search" in fnames
    assert len(payload) <= 5  # tool_search + 4


def test_defer_disabled_when_threshold_zero(tmp_path: Path) -> None:
    reg = _many_low_tools_registry()
    settings = _settings(tmp_path, defer_threshold=0)
    payload, defer, _ = build_openai_tools_for_chat_loop(reg, settings, None, deferred_exposed=None)
    assert defer is False
    assert len(payload) == 31  # 30 tools + tool_search
