"""Unit tests for the SafetySandbox capability gate."""

from __future__ import annotations

import pytest

from agentium.sandbox.safety_sandbox import (
    SafetySandbox,
    SandboxDeniedError,
    SandboxOutputTooLargeError,
    SandboxProfile,
    SandboxRequest,
    SandboxTimeoutError,
)


def test_safety_sandbox_grants_when_capabilities_match():
    audited: list[tuple[str, dict]] = []
    sandbox = SafetySandbox(
        profiles={
            ("tenant-a", "echo"): SandboxProfile(
                allowed_capabilities=frozenset({"compute"}),
            )
        },
        audit=lambda evt, payload: audited.append((evt, dict(payload))),
    )

    request = SandboxRequest(tool_name="echo", tenant_id="tenant-a", capabilities=["compute"])
    outcome = sandbox.run(request, lambda value: value, "hello")

    assert outcome.output == "hello"
    assert outcome.granted == frozenset({"compute"})
    assert any(evt == "sandbox_granted" for evt, _ in audited)


def test_safety_sandbox_blocks_unknown_capability():
    sandbox = SafetySandbox(
        profiles={("tenant-a", "echo"): SandboxProfile(allowed_capabilities=frozenset())}
    )
    request = SandboxRequest(
        tool_name="echo", tenant_id="tenant-a", capabilities=["net.outbound.email"]
    )
    with pytest.raises(SandboxDeniedError):
        sandbox.run(request, lambda: "ok")


def test_safety_sandbox_falls_back_to_global_default():
    sandbox = SafetySandbox(
        profiles={("*", "*"): SandboxProfile(allowed_capabilities=frozenset({"compute"}))}
    )
    request = SandboxRequest(tool_name="any", tenant_id="any", capabilities=["compute"])
    outcome = sandbox.run(request, lambda: 1)
    assert outcome.output == 1


def test_safety_sandbox_enforces_wall_clock_limit():
    ticks = iter([0.0, 5.0])
    sandbox = SafetySandbox(
        profiles={
            ("*", "*"): SandboxProfile(
                allowed_capabilities=frozenset({"compute"}),
                max_wall_seconds=1.0,
            )
        },
        clock=lambda: next(ticks),
    )
    request = SandboxRequest(tool_name="slow", tenant_id="any", capabilities=["compute"])
    with pytest.raises(SandboxTimeoutError):
        sandbox.run(request, lambda: "done")


def test_safety_sandbox_enforces_output_size():
    sandbox = SafetySandbox(
        profiles={
            ("*", "*"): SandboxProfile(
                allowed_capabilities=frozenset({"compute"}),
                max_output_bytes=4,
            )
        }
    )
    request = SandboxRequest(tool_name="big", tenant_id="any", capabilities=["compute"])
    with pytest.raises(SandboxOutputTooLargeError):
        sandbox.run(request, lambda: "this is too big")
