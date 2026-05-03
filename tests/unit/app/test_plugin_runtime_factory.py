"""Unit tests for PluginRuntimeFactory."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.app.plugin_runtime_factory import build_plugin_runtime
from agentium.app.settings import load_settings
from agentium.coordination.artifact_store import ArtifactStore
from agentium.coordination.emergence_guardrails import EmergenceGuardrails
from agentium.coordination.task_graph import TaskGraphSupervisor
from agentium.memory.factory import build_memory_backend
from agentium.app.plugins_config import Mem0PluginConfig, MemoryPluginConfig
from agentium.runtime.deepresearch_pipeline import stub_handlers


def test_build_mem0_backend_fails_when_mem0_import_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "mem0":
            raise ImportError("simulated missing mem0 package")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setenv("MEM0_API_KEY", "dummy-key-for-test")
    cfg = MemoryPluginConfig(
        backend="mem0",
        mem0=Mem0PluginConfig(api_key_from_env="MEM0_API_KEY"),
    )
    with pytest.raises(RuntimeError, match=r"agentium\[mem0\]"):
        build_memory_backend(cfg, tmp_path)


def test_build_memory_backend_mem0_requires_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("M0K", raising=False)
    cfg = MemoryPluginConfig(
        backend="mem0",
        mem0=Mem0PluginConfig(api_key_from_env="M0K"),
    )
    with pytest.raises(ValueError, match="Environment variable"):
        build_memory_backend(cfg, tmp_path)


def test_build_plugin_runtime_wires_proposal_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_AUDIT_BACKEND", "memory")
    monkeypatch.setenv("AGENTIUM_APPROVAL_BACKEND", "memory")
    settings = load_settings()
    handlers = stub_handlers().to_handler_map()
    rt = build_plugin_runtime(
        settings,
        handlers=handlers,
        audit_sink=None,
        artifact_store=ArtifactStore(persist_path=tmp_path / "a.jsonl"),
        guardrails=EmergenceGuardrails(limits={}),
        task_graph=TaskGraphSupervisor(),
    )
    assert rt.proposal_queue is not None
    assert rt.evolution_plugin is not None
    assert rt.fingerprint["orchestration_backend"] == "native"
