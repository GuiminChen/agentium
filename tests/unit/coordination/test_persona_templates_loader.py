"""Tests for bundled persona templates and optional overlay directory."""

from __future__ import annotations

from pathlib import Path

from tests.helpers.app_settings_test_baseline import app_settings_extended_dict_for_data_dir
from tests.helpers.chat_ingress_test_defaults import chat_ingress_off_fields

from agentium.app.plugins_config import load_plugins_config
from agentium.app.settings import AppSettings
from agentium.coordination.persona_templates.loader import load_persona_templates


def _minimal_settings(tmp_path: Path, *, extra_root: Path | None = None) -> AppSettings:
    plugins = tmp_path / "plugins.yaml"
    plugins.write_text(
        "orchestration:\n  backend: native\nmemory:\n  backend: memory\n"
        "evolution:\n  plugin: native\n",
        encoding="utf-8",
    )
    pol = tmp_path / "pol.yaml"
    pol.write_text("version: t\ndefault_decision: deny\ndefault_reason: x\nrules: []\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    usr = tmp_path / "uskills"
    usr.mkdir()
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
        skills_user_root=usr,
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
        persona_templates_extra_root=extra_root,
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


def test_load_persona_templates_includes_bundled_default(tmp_path: Path) -> None:
    settings = _minimal_settings(tmp_path)
    roles = load_persona_templates(settings)
    ids = [r.role_id for r in roles]
    assert "default" in ids
    default = next(r for r in roles if r.role_id == "default")
    assert "IDENTITY" in default.identity_md or "Who you are" in default.identity_md
    assert default.tools_md.strip()


def test_persona_templates_overlay_replaces_same_role_id(tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    role_dir = extra / "default"
    role_dir.mkdir(parents=True)
    (role_dir / "manifest.yaml").write_text(
        "role_id: default\ndisplay_name: Overlay Default\ndescription: test overlay\n",
        encoding="utf-8",
    )
    (role_dir / "IDENTITY.md").write_text("# Overlay identity\n", encoding="utf-8")
    settings = _minimal_settings(tmp_path, extra_root=extra)
    roles = load_persona_templates(settings)
    overlay = next(r for r in roles if r.role_id == "default")
    assert overlay.display_name == "Overlay Default"
    assert overlay.identity_md.strip() == "# Overlay identity"


def test_persona_templates_overlay_adds_role(tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    role_dir = extra / "risk_analyst"
    role_dir.mkdir(parents=True)
    (role_dir / "manifest.yaml").write_text(
        "role_id: risk_analyst\ndisplay_name: Risk analyst\ndescription: custom role\n",
        encoding="utf-8",
    )
    (role_dir / "SOUL.md").write_text("Be cautious.\n", encoding="utf-8")
    settings = _minimal_settings(tmp_path, extra_root=extra)
    roles = load_persona_templates(settings)
    assert any(r.role_id == "risk_analyst" for r in roles)
