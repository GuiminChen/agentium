"""Serializable fingerprint of the process environment for eval reproducibility."""

from __future__ import annotations

import os
import platform
import sys
from typing import Any, Dict


def build_eval_environment_fingerprint(
    *,
    include_agentium_env: bool = True,
) -> Dict[str, Any]:
    """Collect Python/OS signals and optional ``AGENTIUM_*`` env mirror for eval reports.

    Args:
        include_agentium_env: When true, snapshot keys from ``os.environ`` prefixed with
            ``AGENTIUM_`` (values as strings; no secret redaction in this helper).

    Returns:
        JSON-serializable dict suitable for :class:`~agentium.evaluation.eval_runner.EvalSample`.
    """

    agentium_env: Dict[str, str] = {}
    if include_agentium_env:
        for key, val in sorted(os.environ.items()):
            if key.startswith("AGENTIUM_"):
                agentium_env[key] = val
    return {
        "python_implementation": sys.implementation.name,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "cpu_count": os.cpu_count() or 0,
        "agentium_env": agentium_env,
    }
