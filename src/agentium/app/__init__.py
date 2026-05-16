"""Application wiring package."""

from __future__ import annotations

from typing import Any

from agentium.app.settings import AppSettings, ProfileName, load_settings

__all__ = [
    "AppSettings",
    "ProfileName",
    "RuntimeContainer",
    "build_runtime_container",
    "load_settings",
]


def __getattr__(name: str) -> Any:
    """Lazy-import bootstrap so ``from agentium.app.settings import …`` avoids import cycles."""
    if name == "RuntimeContainer":
        from agentium.app.bootstrap import RuntimeContainer as _RuntimeContainer

        return _RuntimeContainer
    if name == "build_runtime_container":
        from agentium.app.bootstrap import build_runtime_container as _build_runtime_container

        return _build_runtime_container
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
