"""Tests for :func:`build_skill_activation_plan` and :func:`execution_outline`."""

from __future__ import annotations

from pathlib import Path

from agentium.skills.plan import build_skill_activation_plan, execution_outline


def test_plan_and_outline_low_signal_query(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    sdir = root / "only-skill"
    sdir.mkdir()
    (sdir / "SKILL.md").write_text(
        "---\nname: only-skill\ndescription: lonely\n---\n",
        encoding="utf-8",
    )
    plan = build_skill_activation_plan("zzz totally unrelated", root, top_k=3)
    assert plan.primary_skill_id is None
    assert plan.ranked[0][1] == 0.0


def test_execution_outline_keys() -> None:
    root = Path("/tmp/skills")  # path unused by outline
    from agentium.skills.plan import SkillActivationPlan

    plan = SkillActivationPlan(
        query="q",
        skills_root=root,
        primary_skill_id="x",
        primary_skill_md=root / "x" / "SKILL.md",
        ranked=(("x", 1.0),),
    )
    body = execution_outline(plan)
    assert "step_load_context" in body
    assert body["plan"]["primary_skill_id"] == "x"
