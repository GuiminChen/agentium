"""Compose optional SKILL.md excerpts into chat system prompts (bounded size)."""

from __future__ import annotations

from agentium.app.settings import AppSettings
from agentium.skills.catalog import load_merged_skill_manifests
from agentium.skills.manifest import skill_markdown_body

WORKSPACE_AGENT_SKILL_TAG = "workspace_agent"


def build_skill_addon_text(skill_tag: str, settings: AppSettings) -> str:
    """Return markdown excerpt appended after ``Bound skill: …`` for known packs.

    The synthetic ``workspace_agent`` tag maps to no extra body (UI/workbench persona).

    Args:
        skill_tag: Session or per-message skill identifier.
        settings: Application settings (skill roots + truncation budget).

    Returns:
        Additional system markdown or empty string.
    """

    tag = (skill_tag or "").strip()
    if not tag or tag == WORKSPACE_AGENT_SKILL_TAG:
        return ""
    manifests = load_merged_skill_manifests(settings)
    for manifest in manifests:
        if manifest.name != tag:
            continue
        body = skill_markdown_body(manifest.skill_md_path)
        limit = settings.chat_skill_body_max_chars
        truncated = False
        if limit > 0 and len(body) > limit:
            body = body[:limit]
            truncated = True
        suffix = "\n\n_(truncated to AGENTIUM_CHAT_SKILL_BODY_MAX_CHARS)_\n" if truncated else ""
        return (
            f"\n\n### Skill pack documentation excerpt (`{tag}`)\n\n{body}{suffix}"
        )
    return ""
