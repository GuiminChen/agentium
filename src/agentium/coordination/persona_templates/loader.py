"""Discover and load persona role folders (manifest + four Markdown planes).

Each role directory contains ``manifest.yaml`` plus optional ``IDENTITY.md``,
``SOUL.md``, ``TOOLS.md``, and ``USER.md``. Extra roles can be added under
``AppSettings.persona_templates_extra_root`` using the same layout.

The four Markdown planes map onto ``workspace_agent.persona_*_md`` fields for chat system prompts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

import yaml

from agentium.app.settings import AppSettings

_ROLE_ID_SAFE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


@dataclass(frozen=True)
class PersonaTemplateRole:
    """One persona template suitable for JSON API responses."""

    role_id: str
    display_name: str
    description: str
    identity_md: str
    soul_md: str
    tools_md: str
    user_md: str

    def as_public_dict(self) -> Dict[str, Any]:
        """Serialize for HTTP ``GET /v1/chat/persona-templates``."""

        return {
            "role_id": self.role_id,
            "display_name": self.display_name,
            "description": self.description,
            "identity_md": self.identity_md,
            "soul_md": self.soul_md,
            "tools_md": self.tools_md,
            "user_md": self.user_md,
        }


def load_persona_templates(settings: AppSettings) -> List[PersonaTemplateRole]:
    """Merge bundled templates with optional filesystem overlay (overlay wins on id)."""

    merged: Dict[str, PersonaTemplateRole] = {}
    for role in _load_roles_from_root(_bundled_roles_root()):
        merged[role.role_id] = role
    extra_root = settings.persona_templates_extra_root
    if extra_root is not None and extra_root.is_dir():
        for role in _load_roles_from_root(extra_root):
            merged[role.role_id] = role
    return _sort_roles(merged.values())


def _bundled_roles_root() -> Path:
    return Path(__file__).resolve().parent / "bundled_roles"


def _sort_roles(roles: Iterable[PersonaTemplateRole]) -> List[PersonaTemplateRole]:
    items = list(roles)

    def _key(r: PersonaTemplateRole) -> tuple[int, str]:
        if r.role_id == "default":
            return (0, r.role_id)
        return (1, r.role_id)

    items.sort(key=_key)
    return items


def _load_roles_from_root(root: Path) -> List[PersonaTemplateRole]:
    if not root.is_dir():
        return []
    out: List[PersonaTemplateRole] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.yaml"
        if not manifest_path.is_file():
            continue
        try:
            role = _load_role_directory(child, manifest_path)
        except ValueError:
            continue
        out.append(role)
    return out


def _load_role_directory(role_dir: Path, manifest_path: Path) -> PersonaTemplateRole:
    raw_manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw_manifest, Mapping):
        raise ValueError("manifest must be a mapping")
    meta = _normalize_manifest(dict(raw_manifest))
    role_id = meta["role_id"]
    if not _ROLE_ID_SAFE.match(role_id):
        raise ValueError(f"invalid role_id: {role_id!r}")
    identity_md = _read_md(role_dir, "IDENTITY.md")
    soul_md = _read_md(role_dir, "SOUL.md")
    tools_md = _read_md(role_dir, "TOOLS.md")
    user_md = _read_md(role_dir, "USER.md")
    return PersonaTemplateRole(
        role_id=role_id,
        display_name=meta["display_name"],
        description=meta["description"],
        identity_md=identity_md,
        soul_md=soul_md,
        tools_md=tools_md,
        user_md=user_md,
    )


def _normalize_manifest(raw: Dict[str, Any]) -> Dict[str, str]:
    rid = str(raw.get("role_id", "")).strip()
    if not rid:
        raise ValueError("role_id required")
    display_name = str(raw.get("display_name", "")).strip()
    if not display_name:
        raise ValueError("display_name required")
    description = str(raw.get("description", "")).strip()
    return {"role_id": rid, "display_name": display_name, "description": description}


def _read_md(role_dir: Path, filename: str) -> str:
    path = role_dir / filename
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    return text
