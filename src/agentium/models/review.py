"""Models used by code review workflow."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodeFile:
    """A changed file payload for AI review."""

    path: Path
    content: str
    changes: str
