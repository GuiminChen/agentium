"""Unit tests for app.settings."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentium.app.settings import AppSettings, load_settings


def test_load_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AGENTIUM_PROFILE", raising=False)
    monkeypatch.delenv("AGENTIUM_HTTP_HOST", raising=False)
    monkeypatch.delenv("AGENTIUM_HTTP_PORT", raising=False)
    monkeypatch.delenv("AGENTIUM_LLM_WIKI_ENABLED", raising=False)
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    # Repo `.env` may pin a model; empty forces coded default (dotenv uses override=False).
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    isolate_dotenv = tmp_path / ".pytest-settings.env"
    isolate_dotenv.write_text("# isolate from developer repo .env during tests\n", encoding="utf-8")
    settings = load_settings(isolate_dotenv)
    assert isinstance(settings, AppSettings)
    assert settings.profile == "dev"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8765
    assert settings.policy_path.name == "runtime_policy.default.yaml"
    assert settings.data_dir == tmp_path.resolve()
    assert settings.plugins_config_path.name == "runtime_plugins.default.yaml"
    assert settings.plugins.orchestration.backend == "native"
    repo_root = Path(__file__).resolve().parents[3]
    plug_yaml = yaml.safe_load(
        (repo_root / "configs" / "runtime_plugins.default.yaml").read_text(encoding="utf-8"),
    )
    assert settings.plugins.llm_wiki.enabled is bool(
        (plug_yaml.get("llm_wiki") or {}).get("enabled"),
    )
    assert settings.chat_skill_body_max_chars == 8000
    assert settings.chat_agent_tools_enabled is False
    assert settings.chat_agent_max_tool_rounds == 8
    assert settings.deepseek_thinking_enabled is True
    assert settings.deepseek_reasoning_effort == "high"
    assert settings.deepseek_inject_think_max_instruction is True
    assert settings.deepseek_dsml_tool_prompt_enabled is True
    assert settings.chat_completion_model == "deepseek-v4-flash"
    assert settings.chat_mid_semantic_memory_enabled is True
    assert settings.chat_session_running_summary_enabled is True
    assert settings.chat_auto_session_title_enabled is True
    assert settings.deferred_tasks_enabled is True
    assert settings.deferred_thread_pool_size == 4
    assert settings.deferred_task_backend == "thread"
    assert settings.redis_url is None
    assert settings.feature_task_lock_enabled is False
    assert settings.task_lock_sqlite_path == (tmp_path / "task_lock.db").resolve()
    assert settings.task_lock_max_ttl_seconds == 3600
    assert settings.harness_oracle_enabled is False
    assert settings.harness_parallel_workers_max == 8
    assert settings.scheduled_jobs_enabled is False
    assert settings.scheduled_jobs_tick_seconds == 30.0
    assert settings.scheduled_jobs_webhook_secret is None
    assert settings.scheduled_jobs_policy_gate_enabled is False
    assert settings.scheduled_job_default_budget_estimate_tokens == 0
    assert settings.execution_tier_high_risk_gate_enabled is False


def test_load_settings_chat_auto_title_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.setenv("AGENTIUM_CHAT_AUTO_TITLE", "0")
    settings = load_settings()
    assert settings.chat_auto_session_title_enabled is False


def test_load_settings_deferred_backend_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.setenv("AGENTIUM_DEFERRED_TASK_BACKEND", "celery")
    settings = load_settings()
    assert settings.deferred_task_backend == "celery"


def test_load_settings_deferred_backend_invalid_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.setenv("AGENTIUM_DEFERRED_TASK_BACKEND", "redis-not-valid")
    settings = load_settings()
    assert settings.deferred_task_backend == "thread"


def test_load_settings_llm_wiki_enabled_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.setenv("AGENTIUM_LLM_WIKI_ENABLED", "1")
    settings = load_settings()
    assert settings.plugins.llm_wiki.enabled is True


def test_load_settings_llm_wiki_force_off_beats_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    cfg_src = repo_root / "configs" / "runtime_plugins.default.yaml"
    plug_data = yaml.safe_load(cfg_src.read_text(encoding="utf-8"))
    plug_data.setdefault("llm_wiki", {})["enabled"] = True
    plug_file = tmp_path / "plugins.yaml"
    plug_file.write_text(yaml.safe_dump(plug_data, sort_keys=False), encoding="utf-8")

    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.setenv("AGENTIUM_PLUGINS_CONFIG", str(plug_file.resolve()))
    monkeypatch.setenv("AGENTIUM_LLM_WIKI_ENABLED", "0")
    monkeypatch.chdir(tmp_path)
    isolate_dotenv = tmp_path / ".pytest-settings.env"
    isolate_dotenv.write_text("# isolate\n", encoding="utf-8")

    settings = load_settings(isolate_dotenv)
    assert settings.plugins.llm_wiki.enabled is False


def test_load_settings_redis_url_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.setenv("AGENTIUM_REDIS_URL", "redis://localhost:6379/0")
    settings = load_settings()
    assert settings.redis_url == "redis://localhost:6379/0"


def test_load_settings_chat_ingress_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.delenv("AGENTIUM_CHAT_INGRESS_BACKEND", raising=False)
    settings = load_settings()
    assert settings.chat_ingress_backend == "off"
    assert settings.chat_ingress_debounce_ms == 500
    assert settings.chat_ingress_queue_cap == 20


def test_load_settings_deferred_tasks_master_switch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.setenv("AGENTIUM_DEFERRED_TASKS", "0")
    settings = load_settings()
    assert settings.deferred_tasks_enabled is False


def test_load_settings_deferred_thread_pool_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.setenv("AGENTIUM_DEFERRED_THREAD_POOL_SIZE", "99")
    settings = load_settings()
    assert settings.deferred_thread_pool_size == 32


def test_load_settings_deferred_thread_pool_invalid_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.setenv("AGENTIUM_DEFERRED_THREAD_POOL_SIZE", "nope")
    settings = load_settings()
    assert settings.deferred_thread_pool_size == 4


def test_load_settings_deepseek_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_DEEPSEEK_THINKING_ENABLED", "0")
    monkeypatch.setenv("AGENTIUM_DEEPSEEK_REASONING_EFFORT", "MAX")
    monkeypatch.setenv("AGENTIUM_DEEPSEEK_THINK_MAX_INSTRUCTION", "0")
    monkeypatch.setenv("AGENTIUM_DEEPSEEK_DSML_TOOL_PROMPT", "0")
    settings = load_settings()
    assert settings.deepseek_thinking_enabled is False
    assert settings.deepseek_reasoning_effort == "max"
    assert settings.deepseek_inject_think_max_instruction is False
    assert settings.deepseek_dsml_tool_prompt_enabled is False


def test_load_settings_chat_model_pro(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "deepseek-v4-pro")
    settings = load_settings()
    assert settings.chat_completion_model == "deepseek-v4-pro"


def test_load_settings_chat_mid_semantic_memory_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MID_SEMANTIC_MEMORY", "0")
    settings = load_settings()
    assert settings.chat_mid_semantic_memory_enabled is False


def test_load_settings_chat_session_summary_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_SESSION_SUMMARY", "0")
    settings = load_settings()
    assert settings.chat_session_running_summary_enabled is False


def test_load_settings_chat_agent_tools_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_AGENT_TOOLS", "1")
    monkeypatch.setenv("AGENTIUM_CHAT_SKILL_BODY_MAX_CHARS", "100")
    monkeypatch.setenv("AGENTIUM_CHAT_AGENT_MAX_TOOL_ROUNDS", "3")
    settings = load_settings()
    assert settings.chat_agent_tools_enabled is True
    assert settings.chat_skill_body_max_chars == 100
    assert settings.chat_agent_max_tool_rounds == 3


def test_load_settings_skills_root_explicit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skills = tmp_path / "custom_skills"
    skills.mkdir()
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_SKILLS_ROOT", str(skills))
    settings = load_settings()
    assert settings.skills_config_root == skills.resolve()
    assert settings.skills_root == skills.resolve()


def test_load_settings_custom_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_PROFILE", "Production")
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_REQUIRE_RUN_MANIFEST", "true")
    monkeypatch.setenv("AGENTIUM_EXPECTED_RUN_MANIFEST_SHA256", "abc123")
    monkeypatch.setenv("AGENTIUM_BACKGROUND_ENABLED", "1")
    settings = load_settings()
    assert settings.profile == "prod"
    assert settings.require_run_manifest is True
    assert settings.expected_run_manifest_sha256 == "abc123"
    assert settings.background_enabled is True


def test_load_settings_choice_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_AUDIT_BACKEND", "garbage")
    settings = load_settings()
    assert settings.audit_backend == "memory"


def test_load_settings_log_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENTIUM_LOG_FILE", raising=False)
    monkeypatch.delenv("AGENTIUM_LOG_BACKUP_COUNT", raising=False)
    monkeypatch.delenv("AGENTIUM_LOG_CONSOLE", raising=False)
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    settings = load_settings()
    assert settings.log_file_path == (tmp_path / "logs" / "agentium.log").resolve()
    assert settings.log_file_backup_count == 14
    assert settings.log_to_console is True


def test_load_settings_log_file_empty_disables_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_LOG_FILE", "")
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    settings = load_settings()
    assert settings.log_file_path is None


def test_load_settings_log_file_custom_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_LOG_FILE", str(tmp_path / "nested" / "app.log"))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    settings = load_settings()
    assert settings.log_file_path == (tmp_path / "nested" / "app.log").resolve()


def test_load_settings_log_backup_invalid_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_LOG_BACKUP_COUNT", "not-int")
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    settings = load_settings()
    assert settings.log_file_backup_count == 14


def test_load_settings_log_console_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_LOG_CONSOLE", "0")
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    settings = load_settings()
    assert settings.log_to_console is False


def test_load_settings_classifier_stage_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.setenv("AGENTIUM_CLASSIFIER_STAGE_ORDER", "a,b,c")
    settings = load_settings()
    assert settings.classifier_stage_order == ("a", "b", "c")


def test_load_settings_task_lock_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.setenv("AGENTIUM_FEATURE_TASK_LOCK", "1")
    monkeypatch.setenv("AGENTIUM_TASK_LOCK_SQLITE_PATH", str(tmp_path / "custom_tl.db"))
    monkeypatch.setenv("AGENTIUM_TASK_LOCK_MAX_TTL_SECONDS", "120")
    monkeypatch.setenv("AGENTIUM_HARNESS_ORACLE_ENABLED", "1")
    monkeypatch.setenv("AGENTIUM_HARNESS_PARALLEL_WORKERS_MAX", "4")
    settings = load_settings()
    assert settings.feature_task_lock_enabled is True
    assert settings.task_lock_sqlite_path == (tmp_path / "custom_tl.db").resolve()
    assert settings.task_lock_max_ttl_seconds == 120
    assert settings.harness_oracle_enabled is True
    assert settings.harness_parallel_workers_max == 4

