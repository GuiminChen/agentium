"""Tests for plugin deferred task registry and factory."""

from __future__ import annotations

from pathlib import Path

import pytest

import agentium.coordination.deferred_tasks.builtins  # noqa: F401 — register builtins
from agentium.coordination.deferred_tasks import KIND_CHAT_GENERATE_SESSION_TITLE, registered_deferred_kinds
from agentium.coordination.deferred_tasks.factory import build_deferred_task_sink
from agentium.coordination.deferred_tasks.registry import register_deferred_handler, run_deferred_handler


def test_builtin_chat_title_kind_registered() -> None:
    kinds = registered_deferred_kinds()
    assert KIND_CHAT_GENERATE_SESSION_TITLE in kinds


def test_register_custom_handler_runs() -> None:
    seen: list[str] = []

    def _h(payload: dict[str, object]) -> None:
        seen.append(str(payload.get("x")))

    register_deferred_handler("coord.test.echo", _h)
    run_deferred_handler("coord.test.echo", {"x": "ping"})
    assert seen == ["ping"]


def test_build_sink_none_when_deferred_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.setenv("AGENTIUM_DEFERRED_TASKS", "0")
    from agentium.app.settings import load_settings

    assert build_deferred_task_sink(load_settings()) is None
