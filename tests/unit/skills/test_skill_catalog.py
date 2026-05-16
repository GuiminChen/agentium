"""Merged skill catalog (project / user / config roots)."""

from __future__ import annotations

from pathlib import Path

from tests.helpers.app_settings_test_baseline import app_settings_extended_dict_for_data_dir
from tests.helpers.chat_ingress_test_defaults import chat_ingress_off_fields

from agentium.app.settings import AppSettings
from agentium.skills.catalog import iter_skill_roots, load_merged_skill_manifests


def _settings_for_catalog(
    tmp_path: Path,
    *,
    project: Path | None,
    user: Path | None,
    config: Path | None,
) -> AppSettings:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    plugins = tmp_path / "plugins.yaml"
    plugins.write_text(
        "orchestration:\n  backend: native\nmemory:\n  backend: memory\n"
        "evolution:\n  plugin: native\n",
        encoding="utf-8",
    )
    from agentium.app.plugins_config import load_plugins_config

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
        skills_project_root=project,
        skills_user_root=user if user is not None else tmp_path / "unused_user",
        skills_config_root=config,
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


def test_catalog_merge_project_wins_over_user(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    proj = repo / "skills"
    user = tmp_path / "home_skills"
    proj.mkdir(parents=True)
    user.mkdir(parents=True)
    (proj / "a-skill").mkdir()
    (proj / "a-skill" / "SKILL.md").write_text(
        "---\nname: a-skill\ndescription: from project\n---\n# P\n",
        encoding="utf-8",
    )
    (user / "a-skill").mkdir()
    (user / "a-skill" / "SKILL.md").write_text(
        "---\nname: a-skill\ndescription: from user\n---\n# U\n",
        encoding="utf-8",
    )
    s = _settings_for_catalog(tmp_path, project=proj, user=user, config=None)
    merged = load_merged_skill_manifests(s)
    assert len(merged) == 1
    assert merged[0].description == "from project"


def test_iter_skill_roots_skips_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    proj = repo / "skills"
    proj.mkdir(parents=True)
    user = tmp_path / "missing_user"
    s = _settings_for_catalog(tmp_path, project=proj, user=user, config=None)
    roots = iter_skill_roots(s)
    assert roots == [proj.resolve()]
