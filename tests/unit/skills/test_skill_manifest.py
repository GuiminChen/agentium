"""Tests for skill manifest parsing."""

from __future__ import annotations

from pathlib import Path

from agentium.skills.manifest import discover_skills, manifest_from_skill_md, skill_markdown_body


def test_manifest_from_skill_md_roundtrip(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    md = skill_dir / "SKILL.md"
    md.write_text(
        "---\n"
        "name: demo-skill\n"
        "description: Demo for unit tests.\n"
        "---\n\n"
        "# Demo\n",
        encoding="utf-8",
    )
    m = manifest_from_skill_md(md)
    assert m.name == "demo-skill"
    assert "unit tests" in m.description
    assert m.skill_dir == skill_dir.resolve()
    assert m.raw.get("name") == "demo-skill"


def test_discover_skills_ignores_hidden(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    (root / ".hidden").mkdir()
    good = root / "a-skill"
    good.mkdir()
    (good / "SKILL.MD").write_text(
        "---\nname: a-skill\ndescription: A\n---\n",
        encoding="utf-8",
    )
    bad = root / "no-md"
    bad.mkdir()
    found = discover_skills(root)
    assert [s.name for s in found] == ["a-skill"]


def test_skill_markdown_body_strips_frontmatter(tmp_path: Path) -> None:
    md = tmp_path / "SKILL.md"
    md.write_text("---\nname: x\ndescription: y\n---\n\nHello\n", encoding="utf-8")
    assert skill_markdown_body(md).strip() == "Hello"

