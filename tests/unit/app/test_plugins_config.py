"""Unit tests for runtime plugins YAML (PluginsConfig)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agentium.app.plugins_config import (
    PluginsConfig,
    load_plugins_config,
    plugins_fingerprint_payload,
)


def test_load_plugins_config_minimal(tmp_path: Path) -> None:
    p = tmp_path / "plugins.yaml"
    p.write_text(
        "orchestration:\n  backend: native\nmemory:\n  backend: memory\nevolution:\n  plugin: native\n",
        encoding="utf-8",
    )
    cfg = load_plugins_config(p)
    assert cfg.orchestration.backend == "native"
    assert cfg.memory.backend == "memory"
    assert cfg.evolution.plugin == "native"


def test_plugins_config_rejects_unknown_root_key(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(
        "orchestration:\n  backend: native\nunknown_root: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_plugins_config(p)


def test_plugins_fingerprint_excludes_mem0_secret_fields() -> None:
    raw = """
orchestration:
  backend: native
memory:
  backend: mem0
  mem0:
    api_key_from_env: "MY_SECRET_REF"
    base_url: "https://example.invalid"
evolution:
  plugin: native
"""
    cfg = PluginsConfig.model_validate(yaml.safe_load(raw))
    fp = plugins_fingerprint_payload(cfg)
    assert fp["memory_backend"] == "mem0"
    assert "MY_SECRET_REF" not in fp.values()
    assert "api_key" not in fp
