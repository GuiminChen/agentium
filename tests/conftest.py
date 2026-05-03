"""Pytest bootstrap configuration."""

from __future__ import annotations

import sys
from pathlib import Path


def _add_src_to_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    src_path = repo_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


_add_src_to_path()
