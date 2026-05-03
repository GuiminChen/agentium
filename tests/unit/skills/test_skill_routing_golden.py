"""Golden-query routing tests against the repo ``skills/`` tree."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentium.skills.manifest import discover_skills
from agentium.skills.plan import build_skill_activation_plan

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = REPO_ROOT / "skills"
GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "skills" / "query_golden.yaml"


pytestmark = pytest.mark.skipif(
    not SKILLS_DIR.is_dir() or not (SKILLS_DIR / "docx" / "SKILL.md").is_file(),
    reason="repo skills/ tree not present",
)


def _golden_cases():
    data = yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8"))
    return data["cases"]


@pytest.mark.parametrize("case", _golden_cases(), ids=lambda c: c["id"])
def test_golden_primary_skill(case: dict) -> None:
    plan = build_skill_activation_plan(case["query"], SKILLS_DIR, top_k=6)
    assert plan.primary_skill_id == case["expect_primary"], (
        f"query={case['query']!r} ranked={plan.ranked}"
    )
