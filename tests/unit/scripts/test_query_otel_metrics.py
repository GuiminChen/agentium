from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_query_otel_metrics_prints_required_queries() -> None:
    script = _REPO_ROOT / "scripts" / "query_otel_metrics.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--prom-base-url",
            "http://prom.example.com",
        ],
        cwd=str(_REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "tool_executions_total" in result.stdout
    assert "tool_latency_p95_ms" in result.stdout
    assert "runtime_turns_total" in result.stdout
    assert "events_total" in result.stdout
