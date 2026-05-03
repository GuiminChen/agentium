"""Tests for :mod:`agentium.app.identity_factory`."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.app.identity_factory import build_identity_provider
from agentium.app.plugins_config import load_plugins_config
from agentium.app.settings import AppSettings
from agentium.governance.access_control import OidcIssuerConfig


def _plugins_file(tmp_path: Path) -> Path:
    p = tmp_path / "plugins.yaml"
    p.write_text(
        "\n".join(
            [
                "orchestration:",
                "  backend: native",
                "memory:",
                "  backend: memory",
                "evolution:",
                "  plugin: native",
            ]
        ),
        encoding="utf-8",
    )
    return p


def _minimal_app_settings(
    tmp_path: Path,
    *,
    profile: str = "dev",
    oidc_issuer_configs: tuple[OidcIssuerConfig, ...] = (),
) -> AppSettings:
    pol = tmp_path / "policy.yaml"
    pol.write_text(
        "\n".join(
            [
                "version: p0",
                "default_decision: deny",
                "default_reason: denied",
                "rules: []",
            ]
        ),
        encoding="utf-8",
    )
    pc = _plugins_file(tmp_path)
    repo = tmp_path / "repo_root"
    repo.mkdir()
    user_skills = tmp_path / "user_skills"
    user_skills.mkdir(parents=True)
    return AppSettings(
        profile=profile,  # type: ignore[arg-type]
        host="127.0.0.1",
        port=8765,
        policy_path=pol,
        data_dir=tmp_path,
        plugins_config_path=pc,
        plugins=load_plugins_config(pc),
        approval_backend="memory",
        audit_backend="memory",
        identity_mode="hybrid",
        require_run_manifest=False,
        expected_run_manifest_sha256=None,
        background_enabled=False,
        background_interval_seconds=30.0,
        background_noise_rps_pause=0.0,
        telemetry_mode="null",
        default_tenant_token_limit=100,
        default_tenant_cost_limit=1.0,
        default_tenant_max_concurrency=2,
        sqlite_approval_ttl_seconds=None,
        emergence_node_warn=1,
        emergence_node_hard=2,
        emergence_outbound_warn=1,
        emergence_outbound_hard=2,
        outbound_rate_limit_per_minute=60,
        policy_release_hmac_secret=None,
        grafana_base_url=None,
        tempo_base_url=None,
        domain_packs_root=None,
        repo_root=repo,
        skills_project_root=None,
        skills_user_root=user_skills,
        skills_config_root=None,
        oidc_issuer_configs=oidc_issuer_configs,
        lsp_upstream_url=None,
        deepseek_api_key=None,
        deepseek_base_url="https://api.deepseek.com",
        chat_completion_model="deepseek-v4",
        chat_completion_timeout_seconds=120.0,
    )


def test_build_identity_provider_empty_returns_none(tmp_path: Path) -> None:
    s = _minimal_app_settings(tmp_path)
    assert build_identity_provider(s) is None


def test_prod_requires_jwks_per_issuer(tmp_path: Path) -> None:
    s = _minimal_app_settings(
        tmp_path,
        profile="prod",
        oidc_issuer_configs=(OidcIssuerConfig(issuer="https://a", audience="api", jwks_url=None),),
    )
    with pytest.raises(ValueError, match="jwks_url"):
        build_identity_provider(s)
