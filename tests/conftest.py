"""Pytest bootstrap configuration."""

from __future__ import annotations

import sys
from pathlib import Path


def _add_src_to_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    crate_src = repo_root / "crate" / "src"
    ordered = [str(repo_root / "src"), str(repo_root)]
    if crate_src.is_dir():
        ordered.append(str(crate_src))
    for path_entry in reversed(ordered):
        if path_entry not in sys.path:
            sys.path.insert(0, path_entry)


_add_src_to_path()
