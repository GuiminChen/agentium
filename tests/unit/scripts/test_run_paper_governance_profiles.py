"""Smoke: paper governance profile script runs and returns 0."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]


def test_run_paper_governance_profiles_exits_zero() -> None:
    script = _ROOT / "scripts" / "run_paper_governance_profiles.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "governance_profiles" in proc.stdout
    assert "allow" in proc.stdout
    assert "require_approval" in proc.stdout
    assert "deny" in proc.stdout
