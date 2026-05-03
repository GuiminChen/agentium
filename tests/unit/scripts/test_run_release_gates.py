"""Smoke tests for scripts.run_release_gates."""

from __future__ import annotations

from pathlib import Path
import json

import pytest


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTIUM_DATA_DIR", str(tmp_path))


def test_run_all_gates_returns_int(tmp_path: Path) -> None:
    from scripts.run_release_gates import run_all_gates

    out = tmp_path / "report.json"
    rc = run_all_gates(output_path=str(out))
    assert isinstance(rc, int)
    assert out.exists()
    report = json.loads(out.read_text(encoding="utf-8"))
    gate_names = {result["name"] for result in report["results"]}
    assert "reliability" in gate_names
