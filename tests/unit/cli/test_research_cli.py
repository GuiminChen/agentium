"""Unit tests for the ``agentium research`` CLI."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

from agentium.cli.commands.research import main as research_main


def test_run_command_prints_report() -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = research_main(["run", "--query", "policy engine"])

    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["success"] is True
    assert payload["report"] is not None
    assert payload["report"]["title"].startswith("Research report")
    assert len(payload["artifacts"]) == 5


def test_run_command_returns_non_zero_when_query_missing() -> None:
    try:
        research_main(["run"])
    except SystemExit as exc:
        assert exc.code == 2
        return
    raise AssertionError("argparse should fail when --query is missing")
