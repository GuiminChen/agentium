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
    chat_completion_model: str
    chat_completion_timeout_seconds: float

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

    if env_file is not None and env_file.exists():
        load_dotenv(env_file)
    else:
        load_dotenv()

    profile = _parse_profile(os.getenv("AGENTIUM_PROFILE"))
    data_dir = Path(os.getenv("AGENTIUM_DATA_DIR", ".agentium_data")).resolve()
    repo_root = Path(__file__).resolve().parents[3]
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
    chat_model = os.getenv("AGENTIUM_CHAT_MODEL", "deepseek-v4").strip() or "deepseek-v4"
    chat_timeout_raw = os.getenv("AGENTIUM_CHAT_COMPLETION_TIMEOUT_SECONDS", "").strip()
    try:
        chat_timeout = float(chat_timeout_raw) if chat_timeout_raw else 120.0
    except ValueError:
        chat_timeout = 120.0

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
    )


TChoice = TypeVar("TChoice", bound=str)


def _parse_choice(value: str, allowed: tuple[TChoice, ...], default: TChoice) -> TChoice:
    cleaned = (value or default).strip().lower()
    if cleaned in allowed:
        return cleaned  # type: ignore[return-value]
    return default
