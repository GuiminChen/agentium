"""CLI wrapper for release gates (implementation: ``agentium.evaluation.release_gates_runner``)."""

from __future__ import annotations

import os

from agentium.evaluation.release_gates_runner import run_all_gates

if __name__ == "__main__":
    output_arg = os.getenv("AGENTIUM_RELEASE_GATE_REPORT")
    raise SystemExit(run_all_gates(output_path=output_arg))
