"""Integration tests for app.bootstrap dependency wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.app import build_runtime_container, load_settings
from agentium.models.context import RequestContext
from agentium.tools.tool_registry import ToolSpec


@pytest.fixture()
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_AUDIT_BACKEND", "memory")
    monkeypatch.setenv("AGENTIUM_APPROVAL_BACKEND", "memory")
    return load_settings()


def test_bootstrap_executes_run_turn(isolated_settings) -> None:
    container = build_runtime_container(isolated_settings)
    context = RequestContext(
        request_id="r-int-1",
        run_id="run-int-1",
        tenant_id="tenant-int",
        user_id="user-int",
        trace_id="trace-int",
    )
    response = container.api.run_turn(context=context, tool_name="echo_tool", args={"text": "hi"})
    assert response.status == "completed"
    container.shutdown()


def test_bootstrap_exposes_phase1_components(isolated_settings) -> None:
    container = build_runtime_container(isolated_settings)
    try:
        assert container.safety_sandbox is not None
        assert container.resource_manager is not None
        assert container.ipc_bus is not None
        assert container.state_observer is not None
        snap = container.state_observer.snapshot()
        probe_names = {p.name for p in snap.probes}
        assert {"policy", "audit", "ipc_bus", "plugins"}.issubset(probe_names)
        assert container.proposal_queue is not None
        assert container.evolution_plugin is not None
        assert "orchestration_backend" in container.plugins_fingerprint
    finally:
        container.shutdown()


def test_bootstrap_default_deny_blocks_unknown_tool(isolated_settings) -> None:
    container = build_runtime_container(isolated_settings)
    container.tool_registry.register(
        ToolSpec(
            name="dangerous",
            capabilities=["external_write"],
            risk_level="high",
            handler=lambda args: {"ok": True},
        )
    )
    context = RequestContext(
        request_id="r-int-2",
        run_id="run-int-2",
        tenant_id="tenant-int",
        user_id="user-int",
        trace_id="trace-int-2",
    )
    response = container.api.run_turn(context=context, tool_name="dangerous", args={})
    assert response.status == "blocked"
    container.shutdown()


def test_bootstrap_wires_appendix_a_runtime_controls(isolated_settings) -> None:
    container = build_runtime_container(isolated_settings)

    assert container.scheduler is not None
    assert container.lifecycle_manager is not None
    assert container.resource_controller is not None

    context = RequestContext(
        request_id="r-int-appendix-a",
        run_id="run-int-appendix-a",
        tenant_id="tenant-int",
        user_id="user-int",
        trace_id="trace-int-appendix-a",
    )

    response = container.api.run_turn(context=context, tool_name="echo_tool", args={"text": "x"})

    assert response.status == "completed"
    assert container.lifecycle_manager.get(context.run_id).state.value == "cleaned"
    container.shutdown()
