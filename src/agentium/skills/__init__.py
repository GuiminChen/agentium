"""Agent Skills: manifests, query routing, and activation planning."""

from agentium.skills.catalog import iter_skill_roots, load_merged_skill_manifests
from agentium.skills.manifest import (
    SkillManifest,
    discover_skills,
    manifest_from_skill_md,
    skill_markdown_body,
)
from agentium.skills.plan import SkillActivationPlan, build_skill_activation_plan, execution_outline
from agentium.skills.routing import primary_skill_for_query, rank_skills_for_query

__all__ = [
    "SkillManifest",
    "SkillActivationPlan",
    "build_skill_activation_plan",
    "iter_skill_roots",
    "load_merged_skill_manifests",
    "discover_skills",
    "execution_outline",
    "manifest_from_skill_md",
    "primary_skill_for_query",
    "rank_skills_for_query",
    "skill_markdown_body",
]
