"""SafetySandbox path / egress metadata gates."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentium.sandbox.safety_sandbox import (
    SandboxDeniedError,
    SandboxProfile,
    SandboxRequest,
    SafetySandbox,
)


def test_path_allowlist_blocks(tmp_path: Path) -> None:
    sb = SafetySandbox()
    sb.register_profile(
        "*",
        "skill_invoke",
        SandboxProfile(
            allowed_capabilities=frozenset(["skill.subprocess"]),
            path_allowlist_prefixes=(str(tmp_path),),
        ),
    )
    req = SandboxRequest(
        tool_name="skill_invoke",
        tenant_id="t1",
        capabilities=["skill.subprocess"],
        metadata={"sandbox_path": "/etc/passwd"},
    )
    with pytest.raises(SandboxDeniedError, match="sandbox_path_not_allowlisted"):
        sb.run(req, lambda: {"ok": True})


def test_path_allowlist_allows_under_prefix(tmp_path: Path) -> None:
    sb = SafetySandbox()
    safe_file = tmp_path / "x.txt"
    safe_file.write_text("a", encoding="utf-8")
    sb.register_profile(
        "*",
        "skill_invoke",
        SandboxProfile(
            allowed_capabilities=frozenset(["skill.subprocess"]),
            path_allowlist_prefixes=(str(tmp_path),),
        ),
    )
    req = SandboxRequest(
        tool_name="skill_invoke",
        tenant_id="t1",
        capabilities=["skill.subprocess"],
        metadata={"sandbox_path": str(safe_file)},
    )
    out = sb.run(req, lambda: {"ok": True})
    assert out.output == {"ok": True}


def test_egress_deny_requires_allow_host() -> None:
    sb = SafetySandbox()
    sb.register_profile(
        "*",
        "skill_invoke",
        SandboxProfile(
            allowed_capabilities=frozenset(["net.http", "skill.subprocess"]),
            egress_deny_by_default=True,
            egress_allow_hosts=frozenset({"api.example.com"}),
        ),
    )
    req = SandboxRequest(
        tool_name="skill_invoke",
        tenant_id="t1",
        capabilities=["net.http"],
        metadata={"egress_host": "evil.test"},
    )
    with pytest.raises(SandboxDeniedError, match="sandbox_egress_host_blocked"):
        sb.run(req, lambda: {"ok": True})
