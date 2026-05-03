"""Merge skill manifests from project, user, and config directories."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, List

from agentium.skills.manifest import SkillManifest, discover_skills

if TYPE_CHECKING:
    from agentium.app.settings import AppSettings


def iter_skill_roots(settings: "AppSettings") -> List[Path]:
    """Ordered roots: project ``skills/``, ``~/.agentium/skills``, ``AGENTIUM_SKILLS_ROOT``.

    Later roots only contribute skills whose ``name`` is not already defined.
    Existence is checked here; missing directories are skipped.
    """

    roots: List[Path] = []
    if settings.skills_project_root is not None and settings.skills_project_root.is_dir():
        roots.append(settings.skills_project_root.resolve())
    user_root = settings.skills_user_root
    if user_root.is_dir():
        roots.append(user_root.resolve())
    if settings.skills_config_root is not None and settings.skills_config_root.is_dir():
        roots.append(settings.skills_config_root.resolve())
    return roots


def load_merged_skill_manifests(settings: "AppSettings") -> List[SkillManifest]:
    """Discover skills from all configured roots; first occurrence of a ``name`` wins."""

    merged: dict[str, SkillManifest] = {}
    for root in iter_skill_roots(settings):
        for manifest in discover_skills(root):
            if manifest.name not in merged:
                merged[manifest.name] = manifest
    return sorted(merged.values(), key=lambda m: m.name.lower())
