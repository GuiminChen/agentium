"""Unit tests for chat SKILL.md addon composition."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.helpers.app_settings_test_baseline import app_settings_extended_dict_for_data_dir
from tests.helpers.chat_ingress_test_defaults import chat_ingress_off_fields

from agentium.app.plugins_config import load_plugins_config
from agentium.app.settings import AppSettings
from agentium.coordination.chat_skill_prompt import (
    WORKSPACE_AGENT_SKILL_TAG,
    build_skill_addon_text,
)


def _minimal_settings(tmp_path: Path, *, body_limit: int = 8000) -> AppSettings:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    plugins = tmp_path / "plugins.yaml"
    plugins.write_text(
        "orchestration:\n  backend: native\nmemory:\n  backend: memory\n"
        "evolution:\n  plugin: native\n",
        encoding="utf-8",
    )
    policy = tmp_path / "pol.yaml"
    policy.write_text(
        "version: t\ndefault_decision: deny\ndefault_reason: x\nrules: []\n", encoding="utf-8"
    )
    return AppSettings(
        profile="dev",  # type: ignore[arg-type]
        host="127.0.0.1",
        port=8765,
        policy_path=policy,
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
        skills_user_root=tmp_path / "uskills",
        skills_config_root=None,
        oidc_issuer_configs=(),
        lsp_upstream_url=None,
        deepseek_api_key=None,
        deepseek_base_url="https://api.deepseek.com",
        chat_completion_model="deepseek-v4-flash",
        chat_completion_timeout_seconds=120.0,
        chat_skill_body_max_chars=body_limit,
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


def test_workspace_agent_tag_returns_empty(tmp_path: Path) -> None:
    settings = _minimal_settings(tmp_path)
    assert build_skill_addon_text(WORKSPACE_AGENT_SKILL_TAG, settings) == ""
    assert build_skill_addon_text("  workspace_agent  ", settings) == ""


def test_unknown_skill_returns_empty(tmp_path: Path) -> None:
    settings = _minimal_settings(tmp_path)
    with patch("agentium.coordination.chat_skill_prompt.load_merged_skill_manifests", return_value=[]):
        assert build_skill_addon_text("no-such-pack", settings) == ""


def test_matched_manifest_inserts_excerpt_and_truncates(tmp_path: Path) -> None:
    settings = _minimal_settings(tmp_path, body_limit=20)
    manifest = SimpleNamespace(name="demo-pack", description="x", skill_md_path=Path("/fake/SKILL.md"))
    body = "abcdefghijklmnopqrstuvwxyz"

    with patch(
        "agentium.coordination.chat_skill_prompt.load_merged_skill_manifests",
        return_value=[manifest],
    ), patch("agentium.coordination.chat_skill_prompt.skill_markdown_body", return_value=body):
        out = build_skill_addon_text("demo-pack", settings)

    assert "### Skill pack documentation excerpt (`demo-pack`)" in out
    assert body[:20] in out
    assert "truncated to AGENTIUM_CHAT_SKILL_BODY_MAX_CHARS" in out
