"""Evaluation-only ablation toggles for paper § controlled experiments.

These flags MUST stay off in production workloads. Defaults are inactive:
no env / ``AGENTIUM_EVALUATION_ABLATION`` unset ⇒ identical behavior vs pre-ablation code.

Variants:

- ``full``: no change vs baseline semantics.
- ``no_manifest``: skip run-manifest tool allowlist enforcement in ``ToolRegistry``.
- ``permissive``: coerce policy outcome to ALLOW after rule evaluation (DLP/access/budget unchanged).
"""

from __future__ import annotations

import os
from typing import Literal

AblationVariant = Literal["full", "no_manifest", "permissive"]


def evaluation_ablation_enabled() -> bool:
    """Return True when ``AGENTIUM_EVALUATION_ABLATION`` is a truthy switch."""

    return os.getenv("AGENTIUM_EVALUATION_ABLATION", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def ablation_variant() -> AblationVariant:
    """Resolved variant; meaningless unless :func:`evaluation_ablation_enabled`.

    Defaults to ``full`` when evaluation ablation is enabled but unset.
    """

    raw = os.getenv("AGENTIUM_ABLATION_VARIANT", "full").strip().lower().replace("-", "_")
    if raw in {"", "full"}:
        return "full"
    if raw in {"no_manifest", "nomanifest"}:
        return "no_manifest"
    if raw == "permissive":
        return "permissive"
    raise ValueError(
        f"Unknown AGENTIUM_ABLATION_VARIANT={raw!r}; expected full|no_manifest|permissive"
    )


def effective_variant() -> AblationVariant | None:
    """Return active variant or None when ablation harness is disabled."""

    if not evaluation_ablation_enabled():
        return None
    return ablation_variant()


def bypass_manifest_allowlist() -> bool:
    """Skip manifest-declared_tools enforcement (evaluation only)."""

    return effective_variant() == "no_manifest"


def coerce_policy_allow() -> bool:
    """Override policy decisions to ALLOW (evaluation only)."""

    return effective_variant() == "permissive"
