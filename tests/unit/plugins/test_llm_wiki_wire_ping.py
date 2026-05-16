"""Unit tests for ``build_llm_wiki_wire_ping_payload``."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.app.settings import load_settings
from agentium.plugins.llm_wiki.service import build_llm_wiki_wire_ping_payload


def test_wire_ping_contains_core_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTIUM_CHAT_MODEL", "")
    monkeypatch.delenv("AGENTIUM_LLM_WIKI_ENABLED", raising=False)

    settings = load_settings()
    payload = build_llm_wiki_wire_ping_payload(settings, service_wired=False)

    assert "service_wired" in payload
    assert payload["service_wired"] is False
    assert "plugins_llm_wiki_enabled_in_effective_settings" in payload
    assert "python_executable" in payload
    assert "crate_import_ok" in payload
    assert isinstance(payload.get("hints"), list)
