"""Phase 2 scale: dev profile must register at least 20 built-in tools (phased-delivery)."""

from __future__ import annotations

from agentium.tools.builtin_defaults import builtin_tool_specs_for_profile


def test_dev_builtin_catalog_has_at_least_twenty_tools() -> None:
    specs = builtin_tool_specs_for_profile("dev")
    assert len(specs) >= 20
    names = {s.name for s in specs}
    assert "echo_tool" in names
    assert "db_export" in names
