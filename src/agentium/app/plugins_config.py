"""Pydantic models for configs/runtime_plugins*.yaml (plugin single source of truth)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field


class LangGraphOrchestrationOptions(BaseModel):
    """Structured options for LangGraph backend (extensible)."""

    model_config = {"extra": "forbid"}


class OrchestrationPluginConfig(BaseModel):
    """Orchestration plugin selection and options."""

    backend: Literal["native", "langgraph"] = "native"
    langgraph: LangGraphOrchestrationOptions = Field(default_factory=LangGraphOrchestrationOptions)

    model_config = {"extra": "forbid"}


class Mem0PluginConfig(BaseModel):
    """Mem0 client parameters (secrets via env references only)."""

    api_key_from_env: str = Field(default="", description="Required when memory.backend=mem0")
    base_url: str = ""
    collection: str = ""

    model_config = {"extra": "forbid"}


class MemoryPluginConfig(BaseModel):
    """Memory backend selection (SQLite path / Mem0 knobs)."""

    backend: Literal["memory", "sqlite", "mem0"] = "memory"
    sqlite_relative_path: str = Field(
        default="memory.sqlite",
        description="Relative to data_dir unless absolute.",
    )
    optional_mem0_lane: bool = Field(
        default=False,
        description=(
            "When primary backend is memory/sqlite, also instantiate Mem0 so sessions can "
            "select memory_plugin=mem0."
        ),
    )
    mem0: Mem0PluginConfig = Field(default_factory=Mem0PluginConfig)

    model_config = {"extra": "forbid"}


class HermesClassEvolutionOptions(BaseModel):
    """Tunables for Hermes-class closed-loop evolution (clean-room implementation)."""

    max_proposals_per_invocation: int = Field(default=8, ge=1, le=256)

    model_config = {"extra": "forbid"}


class EvolutionPluginConfigSection(BaseModel):
    """Self-learning / evolution plugin."""

    plugin: Literal["native", "hermes_class"] = "native"
    http_enabled: bool = False
    hermes_class: HermesClassEvolutionOptions = Field(default_factory=HermesClassEvolutionOptions)

    model_config = {"extra": "forbid"}


class LlmWikiRawStorageConfig(BaseModel):
    """Source-object storage for raw materials (filesystem or COS stub)."""

    backend: Literal["local", "shared_mount", "tencent_cos"] = "local"
    base_path: str = Field(
        default="raw_blob",
        description="Directory under data_dir for local/shared_mount backends.",
    )
    cos_staging_relative_path: str = Field(
        default="cos_wiki_staging",
        description="Directory under data_dir for Tencent COS stub materialization.",
    )

    model_config = {"extra": "forbid"}


class LlmWikiDbConfig(BaseModel):
    """SQLite / PostgreSQL backing store for wiki pages and chunk embeddings."""

    backend: Literal["sqlite", "postgresql"] = "sqlite"
    sqlite_relative_path: str = Field(
        default="llm_wiki.sqlite",
        description="SQLite file relative to data_dir unless absolute.",
    )
    postgresql_conninfo_from_env: str = Field(
        default="",
        description="Env var name holding libpq conninfo when backend=postgresql.",
    )

    model_config = {"extra": "forbid"}


class LlmWikiPluginConfig(BaseModel):
    """Karpathy-style LLM Wiki pipeline backed by the ``crate`` package."""

    enabled: bool = False
    raw_storage: LlmWikiRawStorageConfig = Field(default_factory=LlmWikiRawStorageConfig)
    wiki_db: LlmWikiDbConfig = Field(default_factory=LlmWikiDbConfig)
    vault_relative_path: str = Field(
        default="wiki_vault",
        description="Per-process vault roots live under data_dir/wiki_vault/<tenant_id>/.",
    )
    ingest_extra_roots: list[str] = Field(default_factory=list)
    watch_enabled: bool = False
    debounce_ms: int = Field(default=500, ge=0, le=600_000)
    wiki_search_block_session_when_jobs_pending: bool = Field(
        default=True,
        description=(
            "When True, deny session-scoped wiki_search while ingest jobs for that "
            "session are queued/running (within wiki_pending_job_gate_ttl_seconds)."
        ),
    )
    wiki_pending_job_gate_ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400 * 7,
        description="Jobs newer than this age may block session wiki_search when gate enabled.",
    )
    session_upload_max_decoded_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=4096,
        le=128 * 1024 * 1024,
        description="Max decoded bytes for POST /v1/wiki/session-uploads (JSON base64 payload).",
    )

    model_config = {"extra": "forbid"}


class PluginsConfig(BaseModel):
    """Root document for runtime plugin YAML."""

    orchestration: OrchestrationPluginConfig = Field(default_factory=OrchestrationPluginConfig)
    memory: MemoryPluginConfig = Field(default_factory=MemoryPluginConfig)
    evolution: EvolutionPluginConfigSection = Field(default_factory=EvolutionPluginConfigSection)
    llm_wiki: LlmWikiPluginConfig = Field(default_factory=LlmWikiPluginConfig)

    model_config = {"extra": "forbid"}


def load_plugins_config(path: Path) -> PluginsConfig:
    """Load and validate plugin YAML from ``path``."""

    raw_text = path.read_text(encoding="utf-8")
    data: Any = yaml.safe_load(raw_text)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("plugins config root must be a mapping")
    return PluginsConfig.model_validate(data)


def plugins_fingerprint_payload(plugins: PluginsConfig) -> dict[str, Any]:
    """Serializable, non-secret snapshot for REPRO / observability."""

    return {
        "orchestration_backend": plugins.orchestration.backend,
        "memory_backend": plugins.memory.backend,
        "memory_optional_mem0_lane": plugins.memory.optional_mem0_lane,
        "evolution_plugin": plugins.evolution.plugin,
        "evolution_http_enabled": plugins.evolution.http_enabled,
        "llm_wiki_enabled": plugins.llm_wiki.enabled,
        "llm_wiki_raw_backend": plugins.llm_wiki.raw_storage.backend,
        "llm_wiki_db_backend": plugins.llm_wiki.wiki_db.backend,
        "llm_wiki_watch_enabled": plugins.llm_wiki.watch_enabled,
        "llm_wiki_search_session_pending_gate": plugins.llm_wiki.wiki_search_block_session_when_jobs_pending,
        "llm_wiki_pending_job_gate_ttl_seconds": plugins.llm_wiki.wiki_pending_job_gate_ttl_seconds,
    }


__all__ = [
    "EvolutionPluginConfigSection",
    "LangGraphOrchestrationOptions",
    "LlmWikiDbConfig",
    "LlmWikiPluginConfig",
    "LlmWikiRawStorageConfig",
    "Mem0PluginConfig",
    "MemoryPluginConfig",
    "OrchestrationPluginConfig",
    "PluginsConfig",
    "HermesClassEvolutionOptions",
    "load_plugins_config",
    "plugins_fingerprint_payload",
]
