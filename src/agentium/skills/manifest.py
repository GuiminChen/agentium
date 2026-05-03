"""Load Agent Skills packages (SKILL.md frontmatter) from disk."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


@dataclass(frozen=True)
class SkillManifest:
    """One skill directory with parsed ``SKILL.md`` metadata."""

    name: str
    description: str
    skill_dir: Path
    skill_md_path: Path
    raw: Dict[str, Any]


def parse_skill_frontmatter(skill_md_path: Path) -> Dict[str, Any]:
    """Parse YAML frontmatter from the start of ``SKILL.md``."""

    if yaml is None:
        raise RuntimeError("PyYAML is required to parse skill manifests")
    raw_text = skill_md_path.read_text(encoding="utf-8")
    if not raw_text.lstrip().startswith("---"):
        raise ValueError(f"No YAML frontmatter in {skill_md_path}")
    _, sep, rest = raw_text.partition("---")
    if not sep:
        raise ValueError(f"Malformed frontmatter in {skill_md_path}")
    yaml_part, _, _ = rest.partition("\n---")
    meta = yaml.safe_load(yaml_part)
    if not isinstance(meta, dict):
        raise ValueError(f"Frontmatter must be a mapping in {skill_md_path}")
    return meta


def manifest_from_skill_md(skill_md_path: Path) -> SkillManifest:
    """Build :class:`SkillManifest` from ``.../<skill>/SKILL.md``."""

    meta = parse_skill_frontmatter(skill_md_path)
    name = meta.get("name")
    desc = meta.get("description")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"skill name missing or invalid in {skill_md_path}")
    if not isinstance(desc, str):
        raise ValueError(f"skill description missing or invalid in {skill_md_path}")
    skill_dir = skill_md_path.parent.resolve()
    return SkillManifest(
        name=name.strip(),
        description=desc.strip(),
        skill_dir=skill_dir,
        skill_md_path=skill_md_path.resolve(),
        raw=dict(meta),
    )


def discover_skills(skills_root: Path) -> List[SkillManifest]:
    """Return manifests for every immediate child of ``skills_root`` that has ``SKILL.md``."""

    root = skills_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"skills root is not a directory: {root}")
    out: List[SkillManifest] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        md = child / "SKILL.md"
        if not md.is_file():
            continue
        out.append(manifest_from_skill_md(md))
    return out


def skill_markdown_body(skill_md_path: Path) -> str:
    """Return ``SKILL.md`` content after the YAML frontmatter (first ``---`` block)."""

    text = skill_md_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    i = 1
    while i < len(lines):
        if lines[i].strip() == "---":
            return "".join(lines[i + 1 :])
        i += 1
    return text

