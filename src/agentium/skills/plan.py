"""Turn a user query into a concrete skill activation outline for the harness."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from agentium.skills.manifest import SkillManifest, discover_skills
from agentium.skills.routing import rank_skills_for_query


@dataclass(frozen=True)
class SkillActivationPlan:
    """Result of routing: which skill to load first and the full ranking."""

    query: str
    skills_root: Path
    primary_skill_id: str | None
    primary_skill_md: Path | None
    ranked: Tuple[Tuple[str, float], ...]

    @property
    def ordered_skill_ids(self) -> List[str]:
        return [rid for rid, _ in self.ranked]


def build_skill_activation_plan(
    query: str,
    skills_root: Path | str,
    *,
    top_k: int = 8,
) -> SkillActivationPlan:
    """Discover skills under ``skills_root`` and rank them for ``query``."""

    root = Path(skills_root).resolve()
    skills = discover_skills(root)
    ranked_list = rank_skills_for_query(query, skills, top_n=top_k)
    ranked_ids = tuple((s.name, float(score)) for s, score in ranked_list)
    primary = ranked_list[0][0] if ranked_list and ranked_list[0][1] > 0.0 else None
    primary_md = primary.skill_md_path if primary is not None else None
    primary_id = primary.name if primary is not None else None
    return SkillActivationPlan(
        query=query,
        skills_root=root,
        primary_skill_id=primary_id,
        primary_skill_md=primary_md,
        ranked=ranked_ids,
    )


def execution_outline(plan: SkillActivationPlan) -> dict:
    """Describe how a harness would consume :class:`SkillActivationPlan` (no side effects)."""

    return {
        "step_load_context": (
            "Read primary SKILL.md body into the model context (or attach as tool resource)."
        ),
        "step_attach_scripts": (
            "If the skill package defines scripts/, expose or run them via SafetySandbox "
            "according to PolicyEngine + role_template."
        ),
        "step_secondary": (
            "Optional: include top-2..top-k SKILL.md snippets when scores are within a "
            "threshold of the primary (orchestrator policy)."
        ),
        "plan": {
            "primary_skill_id": plan.primary_skill_id,
            "ranked": list(plan.ranked),
        },
    }
