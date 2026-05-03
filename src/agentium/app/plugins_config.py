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


class PluginsConfig(BaseModel):
    """Root document for runtime plugin YAML."""

    orchestration: OrchestrationPluginConfig = Field(default_factory=OrchestrationPluginConfig)
    memory: MemoryPluginConfig = Field(default_factory=MemoryPluginConfig)
    evolution: EvolutionPluginConfigSection = Field(default_factory=EvolutionPluginConfigSection)

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
        "evolution_plugin": plugins.evolution.plugin,
        "evolution_http_enabled": plugins.evolution.http_enabled,
    }


__all__ = [
    "EvolutionPluginConfigSection",
    "LangGraphOrchestrationOptions",
    "Mem0PluginConfig",
    "MemoryPluginConfig",
    "OrchestrationPluginConfig",
    "PluginsConfig",
    "HermesClassEvolutionOptions",
    "load_plugins_config",
    "plugins_fingerprint_payload",
]
