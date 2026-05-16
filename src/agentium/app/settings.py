"""Environment-backed application settings for unified service startup."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Tuple, TypeVar

from dotenv import load_dotenv
from typing_extensions import Literal

from agentium.governance.access_control import OidcIssuerConfig

if TYPE_CHECKING:
    from agentium.app.plugins_config import PluginsConfig


ProfileName = Literal["dev", "staging", "prod"]


@dataclass(frozen=True)
class AppSettings:
    """Resolved settings for Agentium backend processes."""

    profile: ProfileName
    host: str
    port: int
    policy_path: Path
    data_dir: Path
    plugins_config_path: Path
    plugins: "PluginsConfig"
    approval_backend: Literal["memory", "sqlite"]
    audit_backend: Literal["memory", "sqlite", "jsonl"]
    identity_mode: Literal["hybrid", "header", "bearer"]
    require_run_manifest: bool
    expected_run_manifest_sha256: Optional[str]
    background_enabled: bool  # env: AGENTIUM_BACKGROUND_ENABLED
    background_interval_seconds: float  # env: AGENTIUM_BACKGROUND_INTERVAL_SECONDS
    background_noise_rps_pause: float  # env: AGENTIUM_BACKGROUND_NOISE_RPS_PAUSE (0 = off)
    telemetry_mode: Literal["null", "otel"]
    default_tenant_token_limit: int
    default_tenant_cost_limit: float
    default_tenant_max_concurrency: int
    sqlite_approval_ttl_seconds: Optional[int]
    emergence_node_warn: int
    emergence_node_hard: int
    emergence_outbound_warn: int
    emergence_outbound_hard: int
    outbound_rate_limit_per_minute: int
    policy_release_hmac_secret: Optional[str]
    grafana_base_url: Optional[str]
    tempo_base_url: Optional[str]
    domain_packs_root: Optional[Path]
    repo_root: Path
    skills_project_root: Optional[Path]
    skills_user_root: Path
    skills_config_root: Optional[Path]
    oidc_issuer_configs: Tuple[OidcIssuerConfig, ...]
    lsp_upstream_url: Optional[str]
    deepseek_api_key: Optional[str]
    deepseek_base_url: str
    chat_completion_model: str  # env: AGENTIUM_CHAT_MODEL (deepseek-v4-flash | deepseek-v4-pro)
    chat_completion_timeout_seconds: float
    chat_skill_body_max_chars: int  # env: AGENTIUM_CHAT_SKILL_BODY_MAX_CHARS
    chat_tool_description_max_chars: int  # env: AGENTIUM_CHAT_TOOL_DESCRIPTION_MAX_CHARS (OpenAI tools[])
    tool_contract_min_description_chars: int  # env: AGENTIUM_TOOL_CONTRACT_MIN_DESCRIPTION_CHARS
    chat_context_budget_enabled: bool
    chat_context_soft_token_limit: int
    chat_context_hard_token_limit: int
    chat_context_safe_degrade: bool
    prompt_cache_telemetry_enabled: bool
    prompt_cache_http_header: bool
    tool_approval_auto_enabled: bool
    tool_approval_classifier_model: Optional[str]
    tool_approval_max_auto_denies_per_turn: int
    tool_approval_on_fault: Literal["pending_human", "deny"]
    tool_approval_rule_deny_tools: Tuple[str, ...]
    tool_approval_deny_shell_pattern: Optional[str]
    sandbox_path_allowlist_prefixes: Tuple[str, ...]
    sandbox_egress_deny_by_default: bool
    sandbox_egress_allow_hosts: Tuple[str, ...]
    chat_agent_tools_enabled: bool  # env: AGENTIUM_CHAT_AGENT_TOOLS
    chat_agent_max_tool_rounds: int  # env: AGENTIUM_CHAT_AGENT_MAX_TOOL_ROUNDS
    chat_tool_defer_loading_threshold: int  # env: AGENTIUM_CHAT_TOOL_DEFER_LOADING_THRESHOLD (0 = off)
    chat_tool_search_max_expose: int  # env: AGENTIUM_CHAT_TOOL_SEARCH_MAX_EXPOSE (Top-K schemas in defer mode)
    chat_mid_semantic_memory_enabled: bool  # env: AGENTIUM_CHAT_MID_SEMANTIC_MEMORY (Mem0-style MID facts)
    chat_session_running_summary_enabled: bool  # env: AGENTIUM_CHAT_SESSION_SUMMARY (Hermes-style MID digest)
    workspace_agent_persona_max_chars: int  # env: AGENTIUM_WORKSPACE_AGENT_PERSONA_MAX_CHARS
    workspace_agent_max_skill_tags: int  # env: AGENTIUM_WORKSPACE_AGENT_MAX_SKILL_TAGS
    workspace_agent_max_tool_allowlist: int  # env: AGENTIUM_WORKSPACE_AGENT_MAX_TOOL_ALLOWLIST
    deepseek_thinking_enabled: bool  # env: AGENTIUM_DEEPSEEK_THINKING_ENABLED
    deepseek_reasoning_effort: str  # env: AGENTIUM_DEEPSEEK_REASONING_EFFORT (high|max|low|medium|xhigh)
    deepseek_inject_think_max_instruction: bool  # env: AGENTIUM_DEEPSEEK_THINK_MAX_INSTRUCTION
    deepseek_dsml_tool_prompt_enabled: bool  # env: AGENTIUM_DEEPSEEK_DSML_TOOL_PROMPT
    persona_templates_extra_root: Optional[Path]  # env: AGENTIUM_PERSONA_TEMPLATES_DIR (optional overlay)
    log_file_path: Optional[Path]  # env: AGENTIUM_LOG_FILE (empty = disabled; default logs/agentium.log)
    log_file_backup_count: int  # env: AGENTIUM_LOG_BACKUP_COUNT (TimedRotatingFileHandler backupCount)
    log_to_console: bool  # env: AGENTIUM_LOG_CONSOLE
    chat_auto_session_title_enabled: bool  # env: AGENTIUM_CHAT_AUTO_TITLE (first-turn LLM title)
    deferred_tasks_enabled: bool  # env: AGENTIUM_DEFERRED_TASKS (master switch for plugin deferred queue)
    deferred_task_backend: Literal["thread", "celery"]  # env: AGENTIUM_DEFERRED_TASK_BACKEND
    deferred_thread_pool_size: int  # env: AGENTIUM_DEFERRED_THREAD_POOL_SIZE (in-process pool workers)
    scheduled_jobs_enabled: bool  # env: AGENTIUM_SCHEDULED_JOBS
    scheduled_jobs_tick_seconds: float  # env: AGENTIUM_SCHEDULED_JOBS_TICK_SECONDS
    scheduled_jobs_webhook_secret: Optional[str]  # env: AGENTIUM_SCHEDULED_JOBS_WEBHOOK_SECRET
    scheduled_jobs_policy_gate_enabled: bool  # env: AGENTIUM_SCHEDULED_JOBS_POLICY_GATE
    scheduled_job_default_budget_estimate_tokens: int  # env: AGENTIUM_SCHEDULED_JOB_DEFAULT_BUDGET_ESTIMATE_TOKENS
    redis_url: Optional[str]  # env: AGENTIUM_REDIS_URL (Celery broker/backend)
    chat_ingress_backend: Literal["off", "memory", "redis", "postgresql", "sqlite"]
    chat_ingress_debounce_ms: int
    chat_ingress_queue_cap: int
    chat_ingress_lease_ttl_seconds: float
    chat_ingress_redis_key_prefix: str
    chat_ingress_redis_url: Optional[str]
    chat_ingress_database_url: Optional[str]
    chat_ingress_sqlite_path: Path
    kb_contextual_sqlite_path: Optional[Path]  # None => data_dir/kb_contextual.db at runtime
    classifier_stage_order: Tuple[str, ...]
    code_sidecar_egress_allow_hosts: Tuple[str, ...]
    feature_task_lock_enabled: bool  # env: AGENTIUM_FEATURE_TASK_LOCK
    task_lock_sqlite_path: Path
    task_lock_max_ttl_seconds: int  # env: AGENTIUM_TASK_LOCK_MAX_TTL_SECONDS (cap for renew/acquire)
    harness_oracle_enabled: bool  # env: AGENTIUM_HARNESS_ORACLE_ENABLED
    harness_parallel_workers_max: int  # env: AGENTIUM_HARNESS_PARALLEL_WORKERS_MAX (single source cap)
    strict_harness_handoff_enabled: bool  # env: AGENTIUM_STRICT_HARNESS_HANDOFF (workflow handoff keys)
    execution_tier_high_risk_gate_enabled: bool  # env: AGENTIUM_EXECUTION_TIER_HIGH_RISK_GATE (code-exec × high-risk)
    @property
    def sqlite_db_path(self) -> Path:
        return self.data_dir / "agentium.db"

    @property
    def audit_jsonl_path(self) -> Path:
        return self.data_dir / "audit.jsonl"

    @property
    def skills_root(self) -> Optional[Path]:
        """Backward-compatible alias for :attr:`skills_config_root` (``AGENTIUM_SKILLS_ROOT``)."""

        return self.skills_config_root


def _parse_profile(raw: Optional[str]) -> ProfileName:
    value = (raw or "dev").strip().lower()
    if value in ("development", "local"):
        return "dev"
    if value in ("stage", "stg"):
        return "staging"
    if value in ("production", "live"):
        return "prod"
    if value in ("dev", "staging", "prod"):
        return value  # type: ignore[return-value]
    return "dev"


def _parse_oidc_issuer_configs_json(raw: str) -> Tuple[OidcIssuerConfig, ...]:
    """Parse ``AGENTIUM_OIDC_ISSUERS_JSON`` into issuer configs."""

    import json

    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("root must be a JSON array")
    out: list[OidcIssuerConfig] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("each entry must be an object")
        issuer = str(item.get("issuer", "")).strip()
        audience = str(item.get("audience", "")).strip()
        if not issuer or not audience:
            raise ValueError("issuer and audience are required")
        jwks_raw = item.get("jwks_url")
        jwks_url = str(jwks_raw).strip() if jwks_raw else None
        out.append(
            OidcIssuerConfig(
                issuer=issuer,
                audience=audience,
                jwks_url=jwks_url or None,
                tenant_claim=str(item.get("tenant_claim", "tenant_id")),
                roles_claim=str(item.get("roles_claim", "roles")),
                subject_claim=str(item.get("subject_claim", "sub")),
            )
        )
    return tuple(out)


def _parse_float_env(raw: Optional[str], *, default: float) -> float:
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


def load_settings(env_file: Optional[Path] = None) -> AppSettings:
    """Load settings from environment (optional .env for local dev)."""

    repo_root = Path(__file__).resolve().parents[3]
    if env_file is not None and env_file.exists():
        load_dotenv(env_file)
    else:
        # Repo-root `.env` first so flags like AGENTIUM_LLM_WIKI_ENABLED committed there apply
        # even when the process cwd is e.g. `frontend/`. Second pass loads cwd `.env` with
        # override so a local dir can still deliberately override repo defaults.
        load_dotenv(repo_root / ".env", override=False)
        load_dotenv(override=True)

    profile = _parse_profile(os.getenv("AGENTIUM_PROFILE"))
    data_dir = Path(os.getenv("AGENTIUM_DATA_DIR", ".agentium_data")).resolve()
    policy_default = repo_root / "configs" / "runtime_policy.default.yaml"
    policy_path = Path(os.getenv("AGENTIUM_POLICY_PATH", str(policy_default))).resolve()

    plugins_default = repo_root / "configs" / "runtime_plugins.default.yaml"
    plugins_config_path = Path(
        os.getenv("AGENTIUM_PLUGINS_CONFIG", str(plugins_default))
    ).resolve()

    from agentium.app.plugins_config import load_plugins_config

    if not plugins_config_path.is_file():
        raise FileNotFoundError(
            f"Plugins config not found: {plugins_config_path}. Set AGENTIUM_PLUGINS_CONFIG "
            "or add configs/runtime_plugins.default.yaml."
        )
    plugins = load_plugins_config(plugins_config_path)
    # Overrides `llm_wiki.enabled` without editing YAML (`1|true|yes|on` / `0|false|no|off`).
    _llm_wiki_env = os.getenv("AGENTIUM_LLM_WIKI_ENABLED", "").strip().lower()
    if _llm_wiki_env in ("1", "true", "yes", "on"):
        plugins = plugins.model_copy(
            update={"llm_wiki": plugins.llm_wiki.model_copy(update={"enabled": True})}
        )
    elif _llm_wiki_env in ("0", "false", "no", "off"):
        plugins = plugins.model_copy(
            update={"llm_wiki": plugins.llm_wiki.model_copy(update={"enabled": False})}
        )

    require_manifest = os.getenv("AGENTIUM_REQUIRE_RUN_MANIFEST", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    expected_sha = os.getenv("AGENTIUM_EXPECTED_RUN_MANIFEST_SHA256", "").strip() or None

    ttl_raw = os.getenv("AGENTIUM_APPROVAL_TTL_SECONDS", "").strip()
    approval_ttl: Optional[int] = None
    if ttl_raw:
        try:
            approval_ttl = max(0, int(ttl_raw))
        except ValueError:
            approval_ttl = None

    packs_raw = os.getenv("AGENTIUM_DOMAIN_PACKS_ROOT", "").strip()
    domain_packs_root = Path(packs_raw).resolve() if packs_raw else None

    project_skills = repo_root / "skills"
    skills_project_root = project_skills if project_skills.is_dir() else None

    skills_user_root = (Path.home() / ".agentium" / "skills").resolve()

    skills_cfg_raw = os.getenv("AGENTIUM_SKILLS_ROOT", "").strip()
    skills_config_root = Path(skills_cfg_raw).resolve() if skills_cfg_raw else None

    oidc_json = os.getenv("AGENTIUM_OIDC_ISSUERS_JSON", "").strip()
    if oidc_json:
        try:
            oidc_issuer_configs = _parse_oidc_issuer_configs_json(oidc_json)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid AGENTIUM_OIDC_ISSUERS_JSON: {exc}") from exc
    else:
        oidc_issuer_configs = ()

    deepseek_key = os.getenv("AGENTIUM_DEEPSEEK_API_KEY", "").strip() or None
    deepseek_base = os.getenv("AGENTIUM_DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
    if not deepseek_base:
        deepseek_base = "https://api.deepseek.com"
    # Official DeepSeek-V4 chat completions: ``deepseek-v4-flash`` (default) or ``deepseek-v4-pro``.
    chat_model = os.getenv("AGENTIUM_CHAT_MODEL", "deepseek-v4-flash").strip() or "deepseek-v4-flash"
    chat_timeout_raw = os.getenv("AGENTIUM_CHAT_COMPLETION_TIMEOUT_SECONDS", "").strip()
    try:
        chat_timeout = float(chat_timeout_raw) if chat_timeout_raw else 120.0
    except ValueError:
        chat_timeout = 120.0

    skill_body_raw = os.getenv("AGENTIUM_CHAT_SKILL_BODY_MAX_CHARS", "8000").strip()
    try:
        chat_skill_body_max_chars = max(0, int(skill_body_raw))
    except ValueError:
        chat_skill_body_max_chars = 8000

    tool_desc_raw = os.getenv("AGENTIUM_CHAT_TOOL_DESCRIPTION_MAX_CHARS", "512").strip()
    try:
        chat_tool_description_max_chars = max(32, min(4096, int(tool_desc_raw)))
    except ValueError:
        chat_tool_description_max_chars = 512

    tcd_raw = os.getenv("AGENTIUM_TOOL_CONTRACT_MIN_DESCRIPTION_CHARS", "12").strip()
    try:
        tool_contract_min_description_chars = max(1, min(256, int(tcd_raw)))
    except ValueError:
        tool_contract_min_description_chars = 12

    chat_ctx_raw = os.getenv("AGENTIUM_CHAT_CONTEXT_BUDGET", "1").strip().lower()
    chat_context_budget_enabled = chat_ctx_raw not in {"0", "false", "no", "off"}

    csoft_raw = os.getenv("AGENTIUM_CHAT_CONTEXT_SOFT_TOKENS", "24000").strip()
    try:
        chat_context_soft_token_limit = max(512, int(csoft_raw))
    except ValueError:
        chat_context_soft_token_limit = 24000

    chard_raw = os.getenv("AGENTIUM_CHAT_CONTEXT_HARD_TOKENS", "48000").strip()
    try:
        chat_context_hard_token_limit = max(chat_context_soft_token_limit, int(chard_raw))
    except ValueError:
        chat_context_hard_token_limit = max(chat_context_soft_token_limit, 48000)

    chat_context_safe_degrade = os.getenv("AGENTIUM_CHAT_CONTEXT_SAFE_DEGRADE", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }

    pct_raw = os.getenv("AGENTIUM_PROMPT_CACHE_TELEMETRY", "1").strip().lower()
    prompt_cache_telemetry_enabled = pct_raw not in {"0", "false", "no", "off"}

    pch_raw = os.getenv("AGENTIUM_PROMPT_CACHE_HTTP_HEADER", "0").strip().lower()
    prompt_cache_http_header = pch_raw in {"1", "true", "yes", "on"}

    chat_agent_tools_enabled = os.getenv("AGENTIUM_CHAT_AGENT_TOOLS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    rounds_raw = os.getenv("AGENTIUM_CHAT_AGENT_MAX_TOOL_ROUNDS", "8").strip()
    try:
        chat_agent_max_tool_rounds = max(1, min(32, int(rounds_raw)))
    except ValueError:
        chat_agent_max_tool_rounds = 8

    _defer_ld_raw = os.getenv("AGENTIUM_CHAT_TOOL_DEFER_LOADING_THRESHOLD", "24").strip()
    try:
        chat_tool_defer_loading_threshold = max(0, min(256, int(_defer_ld_raw)))
    except ValueError:
        chat_tool_defer_loading_threshold = 24

    _ts_me_raw = os.getenv("AGENTIUM_CHAT_TOOL_SEARCH_MAX_EXPOSE", "8").strip()
    try:
        chat_tool_search_max_expose = max(1, min(32, int(_ts_me_raw)))
    except ValueError:
        chat_tool_search_max_expose = 8

    chat_mid_semantic_memory_enabled = os.getenv(
        "AGENTIUM_CHAT_MID_SEMANTIC_MEMORY", "1"
    ).strip().lower() not in {"0", "false", "no", "off"}

    chat_session_running_summary_enabled = os.getenv(
        "AGENTIUM_CHAT_SESSION_SUMMARY", "1"
    ).strip().lower() not in {"0", "false", "no", "off"}

    persona_raw = os.getenv("AGENTIUM_WORKSPACE_AGENT_PERSONA_MAX_CHARS", "4096").strip()
    try:
        workspace_agent_persona_max_chars = max(0, int(persona_raw))
    except ValueError:
        workspace_agent_persona_max_chars = 4096

    max_sk_raw = os.getenv("AGENTIUM_WORKSPACE_AGENT_MAX_SKILL_TAGS", "8").strip()
    try:
        workspace_agent_max_skill_tags = max(1, min(16, int(max_sk_raw)))
    except ValueError:
        workspace_agent_max_skill_tags = 8

    max_tl_raw = os.getenv("AGENTIUM_WORKSPACE_AGENT_MAX_TOOL_ALLOWLIST", "24").strip()
    try:
        workspace_agent_max_tool_allowlist = max(1, min(64, int(max_tl_raw)))
    except ValueError:
        workspace_agent_max_tool_allowlist = 24

    deepseek_thinking_enabled = os.getenv("AGENTIUM_DEEPSEEK_THINKING_ENABLED", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    deepseek_reasoning_effort = os.getenv("AGENTIUM_DEEPSEEK_REASONING_EFFORT", "high").strip().lower() or "high"
    deepseek_inject_think_max_instruction = os.getenv(
        "AGENTIUM_DEEPSEEK_THINK_MAX_INSTRUCTION", "1"
    ).strip().lower() not in {"0", "false", "no", "off"}
    deepseek_dsml_tool_prompt_enabled = os.getenv("AGENTIUM_DEEPSEEK_DSML_TOOL_PROMPT", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }

    persona_tpl_raw = os.getenv("AGENTIUM_PERSONA_TEMPLATES_DIR", "").strip()
    persona_templates_extra_root = Path(persona_tpl_raw).resolve() if persona_tpl_raw else None

    log_file_raw = os.getenv("AGENTIUM_LOG_FILE", "logs/agentium.log").strip()
    log_file_path = Path(log_file_raw).expanduser().resolve() if log_file_raw else None

    log_backup_raw = os.getenv("AGENTIUM_LOG_BACKUP_COUNT", "14").strip()
    try:
        log_file_backup_count = max(0, int(log_backup_raw))
    except ValueError:
        log_file_backup_count = 14

    log_to_console = os.getenv("AGENTIUM_LOG_CONSOLE", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }

    chat_auto_session_title_enabled = os.getenv(
        "AGENTIUM_CHAT_AUTO_TITLE", "1"
    ).strip().lower() not in {"0", "false", "no", "off"}

    deferred_tasks_enabled = os.getenv(
        "AGENTIUM_DEFERRED_TASKS", "1"
    ).strip().lower() not in {"0", "false", "no", "off"}

    _defer_raw = os.getenv("AGENTIUM_DEFERRED_TASK_BACKEND", "thread").strip().lower()
    deferred_task_backend = (
        _defer_raw if _defer_raw in ("thread", "celery") else "thread"
    )  # type: ignore[assignment]

    redis_url_val = os.getenv("AGENTIUM_REDIS_URL", "").strip() or None

    _ingress_raw = os.getenv("AGENTIUM_CHAT_INGRESS_BACKEND", "off").strip().lower()
    chat_ingress_backend = (
        _ingress_raw
        if _ingress_raw in ("off", "memory", "redis", "postgresql", "sqlite")
        else "off"
    )  # type: ignore[assignment]

    _deb_raw = os.getenv("AGENTIUM_CHAT_INGRESS_DEBOUNCE_MS", "500").strip()
    try:
        chat_ingress_debounce_ms = max(0, int(_deb_raw))
    except ValueError:
        chat_ingress_debounce_ms = 500

    _cap_raw = os.getenv("AGENTIUM_CHAT_INGRESS_QUEUE_CAP", "20").strip()
    try:
        chat_ingress_queue_cap = max(1, min(200, int(_cap_raw)))
    except ValueError:
        chat_ingress_queue_cap = 20

    _ttl_raw = os.getenv("AGENTIUM_CHAT_INGRESS_LEASE_TTL_SECONDS", "600").strip()
    try:
        chat_ingress_lease_ttl_seconds = max(30.0, float(_ttl_raw))
    except ValueError:
        chat_ingress_lease_ttl_seconds = 600.0

    chat_ingress_redis_key_prefix = os.getenv(
        "AGENTIUM_CHAT_INGRESS_REDIS_KEY_PREFIX", "agentium:ingress"
    ).strip() or "agentium:ingress"
    chat_ingress_redis_url = os.getenv("AGENTIUM_CHAT_INGRESS_REDIS_URL", "").strip() or None
    chat_ingress_database_url = os.getenv("AGENTIUM_CHAT_INGRESS_DATABASE_URL", "").strip() or None

    _csql = os.getenv("AGENTIUM_CHAT_INGRESS_SQLITE_PATH", "").strip()
    if _csql:
        chat_ingress_sqlite_path = Path(_csql).expanduser().resolve()
    else:
        chat_ingress_sqlite_path = (data_dir / "chat_ingress.db").resolve()

    _pool_raw = os.getenv("AGENTIUM_DEFERRED_THREAD_POOL_SIZE", "4").strip()
    try:
        deferred_thread_pool_size = max(1, min(32, int(_pool_raw)))
    except ValueError:
        deferred_thread_pool_size = 4

    scheduled_jobs_enabled = os.getenv(
        "AGENTIUM_SCHEDULED_JOBS", "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    _sj_tick = os.getenv("AGENTIUM_SCHEDULED_JOBS_TICK_SECONDS", "30").strip()
    try:
        scheduled_jobs_tick_seconds = max(5.0, float(_sj_tick))
    except ValueError:
        scheduled_jobs_tick_seconds = 30.0
    scheduled_jobs_webhook_secret = os.getenv(
        "AGENTIUM_SCHEDULED_JOBS_WEBHOOK_SECRET", ""
    ).strip() or None
    scheduled_jobs_policy_gate_enabled = os.getenv(
        "AGENTIUM_SCHEDULED_JOBS_POLICY_GATE", "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    _sj_best = os.getenv("AGENTIUM_SCHEDULED_JOB_DEFAULT_BUDGET_ESTIMATE_TOKENS", "0").strip()
    try:
        scheduled_job_default_budget_estimate_tokens = max(0, min(2_000_000, int(_sj_best)))
    except ValueError:
        scheduled_job_default_budget_estimate_tokens = 0

    tap_raw = os.getenv("AGENTIUM_TOOL_APPROVAL_AUTO", "0").strip().lower()
    tool_approval_auto_enabled = tap_raw in {"1", "true", "yes", "on"}
    tool_approval_classifier_model = (
        os.getenv("AGENTIUM_TOOL_APPROVAL_CLASSIFIER_MODEL", "").strip() or None
    )
    mad_raw = os.getenv("AGENTIUM_TOOL_APPROVAL_MAX_AUTO_DENIES_PER_TURN", "3").strip()
    try:
        tool_approval_max_auto_denies_per_turn = max(1, min(32, int(mad_raw)))
    except ValueError:
        tool_approval_max_auto_denies_per_turn = 3
    _fault_raw = os.getenv("AGENTIUM_TOOL_APPROVAL_ON_FAULT", "pending_human").strip().lower()
    tool_approval_on_fault: Literal["pending_human", "deny"] = (
        "deny" if _fault_raw == "deny" else "pending_human"
    )
    _deny_tools_raw = os.getenv("AGENTIUM_TOOL_APPROVAL_RULE_DENY_TOOLS", "").strip()
    tool_approval_rule_deny_tools = tuple(
        x.strip() for x in _deny_tools_raw.split(",") if x.strip()
    )
    _shell_pat = os.getenv("AGENTIUM_TOOL_APPROVAL_DENY_SHELL_PATTERN", "").strip()
    tool_approval_deny_shell_pattern = _shell_pat if _shell_pat else None

    _sandbox_paths = os.getenv("AGENTIUM_SANDBOX_PATH_ALLOWLIST", "").strip()
    sandbox_path_allowlist_prefixes = tuple(
        p.strip() for p in _sandbox_paths.split(":") if p.strip()
    )
    sandbox_egress_deny_by_default = os.getenv(
        "AGENTIUM_SANDBOX_EGRESS_DENY_DEFAULT", "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    _hosts = os.getenv("AGENTIUM_SANDBOX_EGRESS_ALLOW_HOSTS", "").strip()
    sandbox_egress_allow_hosts = tuple(
        h.strip().lower() for h in _hosts.split(",") if h.strip()
    )

    _cls_raw = os.getenv(
        "AGENTIUM_CLASSIFIER_STAGE_ORDER", "constitutional,dlp,tool_approval"
    ).strip()
    classifier_stage_order = tuple(x.strip() for x in _cls_raw.split(",") if x.strip())
    if not classifier_stage_order:
        classifier_stage_order = ("constitutional", "dlp", "tool_approval")

    _cs_hosts = os.getenv("AGENTIUM_CODE_SIDECAR_EGRESS_ALLOW_HOSTS", "").strip()
    code_sidecar_egress_allow_hosts = tuple(
        h.strip().lower() for h in _cs_hosts.split(",") if h.strip()
    )

    _kb_sql = os.getenv("AGENTIUM_KB_CONTEXTUAL_SQLITE_PATH", "").strip()
    kb_contextual_sqlite_path: Optional[Path] = (
        Path(_kb_sql).expanduser().resolve() if _kb_sql else None
    )

    feature_task_lock_enabled = os.getenv("AGENTIUM_FEATURE_TASK_LOCK", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    _tlsql = os.getenv("AGENTIUM_TASK_LOCK_SQLITE_PATH", "").strip()
    if _tlsql:
        task_lock_sqlite_path = Path(_tlsql).expanduser().resolve()
    else:
        task_lock_sqlite_path = (data_dir / "task_lock.db").resolve()
    _tl_ttl = os.getenv("AGENTIUM_TASK_LOCK_MAX_TTL_SECONDS", "3600").strip()
    try:
        task_lock_max_ttl_seconds = max(30, min(86400, int(_tl_ttl)))
    except ValueError:
        task_lock_max_ttl_seconds = 3600
    harness_oracle_enabled = os.getenv("AGENTIUM_HARNESS_ORACLE_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    _hpwm = os.getenv("AGENTIUM_HARNESS_PARALLEL_WORKERS_MAX", "8").strip()
    try:
        harness_parallel_workers_max = max(1, min(32, int(_hpwm)))
    except ValueError:
        harness_parallel_workers_max = 8
    strict_harness_handoff_enabled = os.getenv(
        "AGENTIUM_STRICT_HARNESS_HANDOFF", "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    execution_tier_high_risk_gate_enabled = os.getenv(
        "AGENTIUM_EXECUTION_TIER_HIGH_RISK_GATE", "0"
    ).strip().lower() in {"1", "true", "yes", "on"}

    return AppSettings(
        profile=profile,
        host=os.getenv("AGENTIUM_HTTP_HOST", "127.0.0.1"),
        port=int(os.getenv("AGENTIUM_HTTP_PORT", "8765")),
        policy_path=policy_path,
        data_dir=data_dir,
        plugins_config_path=plugins_config_path,
        plugins=plugins,
        approval_backend=_parse_choice(
            os.getenv("AGENTIUM_APPROVAL_BACKEND", "memory"), ("memory", "sqlite"), "memory"
        ),
        audit_backend=_parse_choice(
            os.getenv("AGENTIUM_AUDIT_BACKEND", "memory"),
            ("memory", "sqlite", "jsonl"),
            "memory",
        ),
        identity_mode=_parse_choice(
            os.getenv("AGENTIUM_IDENTITY_MODE", "hybrid"),
            ("hybrid", "header", "bearer"),
            "hybrid",
        ),
        require_run_manifest=require_manifest,
        expected_run_manifest_sha256=expected_sha,
        background_enabled=os.getenv("AGENTIUM_BACKGROUND_ENABLED", "").strip().lower()
        in {"1", "true", "yes", "on"},
        background_interval_seconds=float(os.getenv("AGENTIUM_BACKGROUND_INTERVAL_SECONDS", "30")),
        background_noise_rps_pause=_parse_float_env(
            os.getenv("AGENTIUM_BACKGROUND_NOISE_RPS_PAUSE", "0"), default=0.0
        ),
        telemetry_mode=_parse_choice(
            os.getenv("AGENTIUM_TELEMETRY", "null"), ("null", "otel"), "null"
        ),
        default_tenant_token_limit=int(os.getenv("AGENTIUM_DEFAULT_TENANT_TOKEN_LIMIT", "100000")),
        default_tenant_cost_limit=float(os.getenv("AGENTIUM_DEFAULT_TENANT_COST_LIMIT", "1000")),
        default_tenant_max_concurrency=int(os.getenv("AGENTIUM_DEFAULT_TENANT_MAX_CONCURRENCY", "8")),
        sqlite_approval_ttl_seconds=approval_ttl,
        emergence_node_warn=int(os.getenv("AGENTIUM_EMERGENCE_NODE_WARN", "200")),
        emergence_node_hard=int(os.getenv("AGENTIUM_EMERGENCE_NODE_HARD", "500")),
        emergence_outbound_warn=int(os.getenv("AGENTIUM_EMERGENCE_OUTBOUND_WARN", "30")),
        emergence_outbound_hard=int(os.getenv("AGENTIUM_EMERGENCE_OUTBOUND_HARD", "60")),
        outbound_rate_limit_per_minute=int(
            os.getenv("AGENTIUM_OUTBOUND_RATE_LIMIT_PER_MINUTE", "60")
        ),
        policy_release_hmac_secret=(
            os.getenv("AGENTIUM_POLICY_RELEASE_HMAC_SECRET", "").strip() or None
        ),
        grafana_base_url=os.getenv("AGENTIUM_GRAFANA_BASE_URL", "").strip() or None,
        tempo_base_url=os.getenv("AGENTIUM_TEMPO_BASE_URL", "").strip() or None,
        domain_packs_root=domain_packs_root,
        repo_root=repo_root,
        skills_project_root=skills_project_root,
        skills_user_root=skills_user_root,
        skills_config_root=skills_config_root,
        oidc_issuer_configs=oidc_issuer_configs,
        lsp_upstream_url=os.getenv("AGENTIUM_LSP_UPSTREAM_URL", "").strip() or None,
        deepseek_api_key=deepseek_key,
        deepseek_base_url=deepseek_base.rstrip("/"),
        chat_completion_model=chat_model,
        chat_completion_timeout_seconds=max(5.0, chat_timeout),
        chat_skill_body_max_chars=chat_skill_body_max_chars,
        chat_tool_description_max_chars=chat_tool_description_max_chars,
        tool_contract_min_description_chars=tool_contract_min_description_chars,
        chat_context_budget_enabled=chat_context_budget_enabled,
        chat_context_soft_token_limit=chat_context_soft_token_limit,
        chat_context_hard_token_limit=chat_context_hard_token_limit,
        chat_context_safe_degrade=chat_context_safe_degrade,
        prompt_cache_telemetry_enabled=prompt_cache_telemetry_enabled,
        prompt_cache_http_header=prompt_cache_http_header,
        tool_approval_auto_enabled=tool_approval_auto_enabled,
        tool_approval_classifier_model=tool_approval_classifier_model,
        tool_approval_max_auto_denies_per_turn=tool_approval_max_auto_denies_per_turn,
        tool_approval_on_fault=tool_approval_on_fault,
        tool_approval_rule_deny_tools=tool_approval_rule_deny_tools,
        tool_approval_deny_shell_pattern=tool_approval_deny_shell_pattern,
        sandbox_path_allowlist_prefixes=sandbox_path_allowlist_prefixes,
        sandbox_egress_deny_by_default=sandbox_egress_deny_by_default,
        sandbox_egress_allow_hosts=sandbox_egress_allow_hosts,
        chat_agent_tools_enabled=chat_agent_tools_enabled,
        chat_agent_max_tool_rounds=chat_agent_max_tool_rounds,
        chat_tool_defer_loading_threshold=chat_tool_defer_loading_threshold,
        chat_tool_search_max_expose=chat_tool_search_max_expose,
        chat_mid_semantic_memory_enabled=chat_mid_semantic_memory_enabled,
        chat_session_running_summary_enabled=chat_session_running_summary_enabled,
        workspace_agent_persona_max_chars=workspace_agent_persona_max_chars,
        workspace_agent_max_skill_tags=workspace_agent_max_skill_tags,
        workspace_agent_max_tool_allowlist=workspace_agent_max_tool_allowlist,
        deepseek_thinking_enabled=deepseek_thinking_enabled,
        deepseek_reasoning_effort=deepseek_reasoning_effort,
        deepseek_inject_think_max_instruction=deepseek_inject_think_max_instruction,
        deepseek_dsml_tool_prompt_enabled=deepseek_dsml_tool_prompt_enabled,
        persona_templates_extra_root=persona_templates_extra_root,
        log_file_path=log_file_path,
        log_file_backup_count=log_file_backup_count,
        log_to_console=log_to_console,
        chat_auto_session_title_enabled=chat_auto_session_title_enabled,
        deferred_tasks_enabled=deferred_tasks_enabled,
        deferred_task_backend=deferred_task_backend,
        deferred_thread_pool_size=deferred_thread_pool_size,
        scheduled_jobs_enabled=scheduled_jobs_enabled,
        scheduled_jobs_tick_seconds=scheduled_jobs_tick_seconds,
        scheduled_jobs_webhook_secret=scheduled_jobs_webhook_secret,
        scheduled_jobs_policy_gate_enabled=scheduled_jobs_policy_gate_enabled,
        scheduled_job_default_budget_estimate_tokens=scheduled_job_default_budget_estimate_tokens,
        redis_url=redis_url_val,
        chat_ingress_backend=chat_ingress_backend,
        chat_ingress_debounce_ms=chat_ingress_debounce_ms,
        chat_ingress_queue_cap=chat_ingress_queue_cap,
        chat_ingress_lease_ttl_seconds=chat_ingress_lease_ttl_seconds,
        chat_ingress_redis_key_prefix=chat_ingress_redis_key_prefix,
        chat_ingress_redis_url=chat_ingress_redis_url,
        chat_ingress_database_url=chat_ingress_database_url,
        chat_ingress_sqlite_path=chat_ingress_sqlite_path,
        kb_contextual_sqlite_path=kb_contextual_sqlite_path,
        classifier_stage_order=classifier_stage_order,
        code_sidecar_egress_allow_hosts=code_sidecar_egress_allow_hosts,
        feature_task_lock_enabled=feature_task_lock_enabled,
        task_lock_sqlite_path=task_lock_sqlite_path,
        task_lock_max_ttl_seconds=task_lock_max_ttl_seconds,
        harness_oracle_enabled=harness_oracle_enabled,
        harness_parallel_workers_max=harness_parallel_workers_max,
        strict_harness_handoff_enabled=strict_harness_handoff_enabled,
        execution_tier_high_risk_gate_enabled=execution_tier_high_risk_gate_enabled,
    )


TChoice = TypeVar("TChoice", bound=str)


def _parse_choice(value: str, allowed: tuple[TChoice, ...], default: TChoice) -> TChoice:
    cleaned = (value or default).strip().lower()
    if cleaned in allowed:
        return cleaned  # type: ignore[return-value]
    return default
