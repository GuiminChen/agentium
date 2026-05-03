"""Unit tests for app.settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.app.settings import AppSettings, load_settings


def test_load_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AGENTIUM_PROFILE", raising=False)
    monkeypatch.delenv("AGENTIUM_HTTP_HOST", raising=False)
    monkeypatch.delenv("AGENTIUM_HTTP_PORT", raising=False)
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))
    settings = load_settings()
    assert isinstance(settings, AppSettings)
    assert settings.profile == "dev"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8765
    assert settings.policy_path.name == "runtime_policy.default.yaml"
    assert settings.data_dir == tmp_path.resolve()
    assert settings.plugins_config_path.name == "runtime_plugins.default.yaml"
    assert settings.plugins.orchestration.backend == "native"


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
