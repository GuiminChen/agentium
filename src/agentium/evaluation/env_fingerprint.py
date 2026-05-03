"""Environment fingerprint capture for reproducible eval reports."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class EnvFingerprint:
    """Snapshot of runtime environment used for eval reproducibility."""

    git_revision: Optional[str]
    git_dirty: bool
    python_version: str
    platform: str
    packages: List[str] = field(default_factory=list)
    extras: Dict[str, str] = field(default_factory=dict)


def capture_env_fingerprint(extras: Optional[Dict[str, str]] = None) -> EnvFingerprint:
    """Capture a static snapshot of the environment for reporting."""

    git_revision = _safe_subprocess(["git", "rev-parse", "HEAD"])
    git_status = _safe_subprocess(["git", "status", "--porcelain"]) or ""
    git_dirty = bool(git_status.strip()) if git_revision else False
    packages = _list_packages()
    return EnvFingerprint(
        git_revision=git_revision.strip() if git_revision else None,
        git_dirty=git_dirty,
        python_version=sys.version.split(" ")[0],
        platform=platform.platform(),
        packages=packages,
        extras=dict(extras or {}),
    )


def _safe_subprocess(args: List[str]) -> Optional[str]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
            shell=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _list_packages() -> List[str]:
    try:
        from importlib.metadata import distributions
    except ImportError:  # pragma: no cover
        return []
    pkgs: List[str] = []
    for dist in distributions():
        try:
            pkgs.append(f"{dist.metadata['Name']}=={dist.version}")
        except Exception:
            continue
    pkgs.sort()
    return pkgs
